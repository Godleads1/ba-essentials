from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.permissions import QUOTE_SEND, require_perm
from apps.audit import services as audit_service
from apps.billing import services as billing
from apps.billing.models import Invoice, UsageKind, UsageLedger
from apps.core import idempotency
from apps.core.exceptions import InvalidTransition, ValidationFailure
from apps.tasks import transitions
from apps.tasks.models import Task, TaskStatus
from apps.tasks.transitions import Action

from .models import Quote, QuoteAcceptance, QuoteStatus, QuoteVersion


def terms_fingerprint(version: QuoteVersion) -> str:
    material = "|".join(
        [
            str(version.pk),
            str(version.version_number),
            version.scope_summary,
            version.exclusions,
            str(version.amount_minor),
            version.currency,
            str(version.turnaround_days),
            str(version.revision_allowance),
            version.payment_terms,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@transaction.atomic
def create_version(
    task: Task,
    actor,
    *,
    scope_summary: str,
    exclusions: str,
    amount_minor: int,
    turnaround_days: int,
    revision_allowance: int,
    payment_terms: str = "",
) -> QuoteVersion:
    require_perm(actor, QUOTE_SEND)
    if task.status != TaskStatus.SCOPING:
        raise InvalidTransition("Quotes can only be prepared while a request is in scoping.")
    if not (scope_summary or "").strip():
        raise ValidationFailure("Describe the scope before saving a quote.")
    if amount_minor is None or amount_minor < 0:
        raise ValidationFailure("Enter a non-negative amount.")
    if not 1 <= turnaround_days <= 365:
        raise ValidationFailure("Turnaround must be between 1 and 365 days.")
    if not 0 <= revision_allowance <= 10:
        raise ValidationFailure("Revision allowance must be between 0 and 10.")

    quote, _ = Quote.objects.get_or_create(task=task, defaults={"created_by": actor})
    next_number = (quote.versions.aggregate(highest=Max("version_number"))["highest"] or 0) + 1
    version = QuoteVersion.objects.create(
        quote=quote,
        version_number=next_number,
        scope_summary=scope_summary.strip(),
        exclusions=(exclusions or "").strip(),
        amount_minor=amount_minor,
        currency=settings.PAYSTACK_BASE_CURRENCY,
        turnaround_days=turnaround_days,
        revision_allowance=revision_allowance,
        payment_terms=(payment_terms or "").strip(),
        created_by=actor,
    )
    audit_service.record(
        action="quote.version_created",
        actor=actor,
        organization_id=task.organization_id,
        resource_type="quote_version",
        resource_id=str(version.pk),
        metadata={"version": next_number, "amount_minor": amount_minor},
    )
    return version


@transaction.atomic
def send_version(task: Task, version: QuoteVersion, actor) -> QuoteVersion:
    require_perm(actor, QUOTE_SEND)
    version = QuoteVersion.objects.select_for_update().get(pk=version.pk)
    if version.quote.task_id != task.pk:
        raise ValidationFailure("That quote belongs to another request.")
    if version.status != QuoteStatus.DRAFT:
        raise ValidationFailure("Only a draft quote can be sent.")
    task = Task.objects.select_for_update().get(pk=task.pk)
    if task.status != TaskStatus.SCOPING:
        raise InvalidTransition("This request is not in scoping.")

    QuoteVersion.objects.filter(quote=version.quote, status=QuoteStatus.SENT).update(
        status=QuoteStatus.SUPERSEDED
    )
    now = timezone.now()
    version.status = QuoteStatus.SENT
    version.sent_at = now
    version.expires_at = now + timedelta(days=settings.QUOTE_VALIDITY_DAYS)
    version.save(update_fields=["status", "sent_at", "expires_at"])

    transitions.transition(
        task=task,
        action=Action.SEND_QUOTE,
        actor=actor,
        reason=f"Quote v{version.version_number} sent",
    )
    return version


@dataclass(frozen=True)
class AcceptanceOutcome:
    covered_by_allowance: bool
    invoice: Invoice | None
    replayed: bool = False


def accept_quote(
    *, task: Task, version: QuoteVersion, actor, idempotency_key: str
) -> AcceptanceOutcome:
    scope = f"quote.accept.{version.pk}"
    fingerprint = f"{task.reference}|{version.pk}|{version.amount_minor}|{version.currency}"

    # Expiry is persisted before the acceptance attempt starts so the quote is
    # closed even though the attempt itself fails and rolls back.
    with transaction.atomic():
        expiring = (
            QuoteVersion.objects.select_for_update()
            .filter(pk=version.pk, status=QuoteStatus.SENT, quote__task_id=task.pk)
            .first()
        )
        expired = expiring is not None and (
            expiring.expires_at is None or expiring.expires_at <= timezone.now()
        )
        if expired:
            expiring.status = QuoteStatus.EXPIRED
            expiring.save(update_fields=["status"])
    if expired:
        raise ValidationFailure(
            "This quote has expired. Ask the team for an updated quote."
        )

    with transaction.atomic():
        reserved = idempotency.reserve(
            user=actor, scope=scope, key=idempotency_key, request_fingerprint=fingerprint
        )
        if reserved.replayed:
            covered = UsageLedger.objects.filter(
                task=task, kind=UsageKind.ALLOWANCE
            ).exists()
            invoice = Invoice.objects.filter(task=task, quote_version=version).first()
            return AcceptanceOutcome(
                covered_by_allowance=covered, invoice=invoice, replayed=True
            )

        locked_task = Task.objects.select_for_update().get(pk=task.pk)
        locked_version = QuoteVersion.objects.select_for_update().get(pk=version.pk)
        if locked_version.quote.task_id != locked_task.pk:
            raise ValidationFailure("That quote belongs to another request.")
        if locked_task.status != TaskStatus.AWAITING_CLIENT_APPROVAL:
            raise InvalidTransition("This request is not awaiting quote approval.")
        if locked_version.status != QuoteStatus.SENT:
            raise ValidationFailure("That quote version is no longer open for acceptance.")

        QuoteAcceptance.objects.create(
            quote_version=locked_version,
            accepted_by=actor,
            terms_fingerprint=terms_fingerprint(locked_version),
        )
        locked_version.status = QuoteStatus.ACCEPTED
        locked_version.save(update_fields=["status"])

        covered = locked_version.amount_minor == 0 or billing.reserve_allowance(
            locked_task
        )
        invoice: Invoice | None = None
        fields = {"revision_allowance_remaining": locked_version.revision_allowance}
        if covered:
            transitions.transition(
                task=locked_task,
                action=Action.QUOTE_ACCEPTED_COVERED,
                actor=actor,
                reason=f"Quote v{locked_version.version_number} accepted",
                fields=fields,
            )
        else:
            invoice = billing.ensure_invoice(locked_task, locked_version)
            transitions.transition(
                task=locked_task,
                action=Action.QUOTE_ACCEPTED_PAYMENT_REQUIRED,
                actor=actor,
                reason=f"Quote v{locked_version.version_number} accepted; payment required",
                fields=fields,
            )

        audit_service.record(
            action="quote.accepted",
            actor=actor,
            organization_id=locked_task.organization_id,
            resource_type="quote_version",
            resource_id=str(locked_version.pk),
            metadata={
                "version": locked_version.version_number,
                "amount_minor": locked_version.amount_minor,
                "covered": covered,
            },
        )
        idempotency.complete(
            user=actor, scope=scope, key=idempotency_key, reference=locked_task.reference
        )
        return AcceptanceOutcome(covered_by_allowance=covered, invoice=invoice)


def reject_quote(*, task: Task, version: QuoteVersion, actor, reason: str) -> Task:
    if (reason or "").strip() == "":
        raise ValidationFailure("Tell the team why the quote was not accepted.")
    with transaction.atomic():
        locked_version = QuoteVersion.objects.select_for_update().get(pk=version.pk)
        if locked_version.quote.task_id != task.pk or locked_version.status != QuoteStatus.SENT:
            raise ValidationFailure("That quote is not open for a decision.")
        locked_version.status = QuoteStatus.REJECTED
        locked_version.save(update_fields=["status"])
        result = transitions.transition(
            task=task,
            action=Action.QUOTE_REJECTED,
            actor=actor,
            reason=reason,
        )
        audit_service.record(
            action="quote.rejected",
            actor=actor,
            organization_id=task.organization_id,
            resource_type="quote_version",
            resource_id=str(locked_version.pk),
            reason=reason,
        )
    return result
