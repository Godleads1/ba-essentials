from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit import services as audit_service
from apps.audit.models import AuditEvent
from apps.core.exceptions import (
    PermissionFailure,
    ValidationFailure,
)
from apps.core.sequences import next_value
from apps.quotes.models import Quote, QuoteStatus

from .models import (
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
    PaymentStatus,
    ProviderWebhookEvent,
    Subscription,
    SubscriptionStatus,
    UsageKind,
    UsageLedger,
)

logger = logging.getLogger("ba.billing")


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    reason: str
    invoice: Invoice | None = None


def active_subscription(organization) -> Subscription | None:
    now = timezone.now()
    subscription = Subscription.objects.filter(
        organization=organization, status=SubscriptionStatus.ACTIVE
    ).select_related("plan").first()
    if subscription is None:
        return None
    if not (subscription.period_start <= now <= subscription.period_end):
        return None
    return subscription


def allowance_remaining(organization) -> int:
    subscription = active_subscription(organization)
    if subscription is None:
        return 0
    used = (
        UsageLedger.objects.filter(
            organization=organization,
            kind=UsageKind.ALLOWANCE,
            period_start=subscription.period_start,
        ).aggregate(total=Sum("units"))["total"]
        or 0
    )
    return max(0, subscription.plan.monthly_task_allowance - used)


def reserve_allowance(task) -> bool:
    """Reserve one plan task under a row lock, or return False if exhausted."""
    now = timezone.now()
    with transaction.atomic():
        subscription = (
            Subscription.objects.select_for_update()
            .filter(organization_id=task.organization_id, status=SubscriptionStatus.ACTIVE)
            .select_related("plan")
            .first()
        )
        if subscription is None or not (
            subscription.period_start <= now <= subscription.period_end
        ):
            return False
        used = (
            UsageLedger.objects.filter(
                organization_id=task.organization_id,
                kind=UsageKind.ALLOWANCE,
                period_start=subscription.period_start,
            ).aggregate(total=Sum("units"))["total"]
            or 0
        )
        if used + 1 > subscription.plan.monthly_task_allowance:
            return False
        try:
            UsageLedger.objects.create(
                organization_id=task.organization_id,
                task=task,
                kind=UsageKind.ALLOWANCE,
                units=1,
                period_start=subscription.period_start,
            )
        except IntegrityError:
            return True
    audit_service.record(
        action="billing.allowance_reserved",
        actor=None,
        organization_id=task.organization_id,
        resource_type="task",
        resource_id=str(task.pk),
    )
    return True


def release_allowance(task) -> None:
    UsageLedger.objects.filter(task=task, kind=UsageKind.ALLOWANCE).delete()


def ensure_invoice(task, quote_version) -> Invoice:
    existing = Invoice.objects.filter(task=task, quote_version=quote_version).first()
    if existing is not None:
        return existing
    number = next_value("invoice.number", start=1000)
    invoice = Invoice.objects.create(
        number=f"INV-{timezone.now():%Y}-{number}",
        organization_id=task.organization_id,
        task=task,
        quote_version=quote_version,
        amount_minor=quote_version.amount_minor,
        currency=quote_version.currency,
        status=InvoiceStatus.ISSUED,
        due_at=timezone.now() + timedelta(days=14),
    )
    audit_service.record(
        action="billing.invoice_issued",
        actor=None,
        organization_id=task.organization_id,
        resource_type="invoice",
        resource_id=str(invoice.pk),
        metadata={"amount_minor": invoice.amount_minor, "currency": invoice.currency},
    )
    return invoice


def eligibility(task) -> Eligibility:
    if UsageLedger.objects.filter(task=task, kind=UsageKind.ALLOWANCE).exists():
        return Eligibility(True, "Covered by the plan allowance.")

    quote = Quote.objects.filter(task=task).first()
    accepted = (
        quote.versions.filter(status=QuoteStatus.ACCEPTED).order_by("-version_number").first()
        if quote
        else None
    )
    if accepted is not None and accepted.amount_minor == 0:
        return Eligibility(True, "No charge for this request.")

    paid = (
        Invoice.objects.filter(task=task, status=InvoiceStatus.PAID)
        .order_by("-issued_at")
        .first()
    )
    if paid is not None:
        return Eligibility(True, "Invoice paid.", paid)

    outstanding = (
        Invoice.objects.filter(task=task, status=InvoiceStatus.ISSUED)
        .order_by("-issued_at")
        .first()
    )
    if outstanding is not None:
        return Eligibility(False, "Payment is still outstanding.", outstanding)

    return Eligibility(False, "No plan allowance or paid invoice is on record.")


def _advance_task_after_payment(invoice: Invoice, actor, *, source: str) -> None:
    """Move a task out of awaiting-payment once the invoice is genuinely paid."""
    if invoice.task_id is None:
        return
    from apps.tasks import transitions
    from apps.tasks.models import Task, TaskStatus
    from apps.tasks.transitions import Action

    task = Task.objects.get(pk=invoice.task_id)
    if task.status != TaskStatus.AWAITING_PAYMENT:
        return
    transitions.transition(
        task=task,
        action=Action.PAYMENT_VERIFIED,
        actor=actor,
        system=actor is None,
        reason=source,
    )


def verify_payment(
    invoice: Invoice, actor, *, reference: str, amount_minor: int, currency: str
) -> PaymentAttempt:
    """Finance verification path: mark paid and release the task for assignment."""
    attempt = record_offline_payment(
        invoice, actor, reference=reference, amount_minor=amount_minor, currency=currency
    )
    _advance_task_after_payment(invoice, actor, source="Payment verified by finance")
    return attempt


def record_offline_payment(
    invoice: Invoice, actor, *, reference: str, amount_minor: int, currency: str
) -> PaymentAttempt:
    """Finance confirms a bank transfer against an invoice, with evidence."""
    reference = (reference or "").strip()
    if len(reference) < 4:
        raise ValidationFailure("Enter the payment reference from the bank transfer.")
    if invoice.status != InvoiceStatus.ISSUED:
        raise ValidationFailure("Only unpaid invoices can be marked as paid.")
    if amount_minor != invoice.amount_minor or currency != invoice.currency:
        raise ValidationFailure(
            "The amount or currency does not match the invoice exactly."
        )

    with transaction.atomic():
        attempt = PaymentAttempt.objects.filter(reference=reference).first()
        if attempt is not None and attempt.invoice_id != invoice.pk:
            raise ValidationFailure("That reference is already used by another invoice.")
        if attempt is None:
            attempt = PaymentAttempt.objects.create(
                invoice=invoice,
                provider="manual",
                reference=reference,
                amount_minor=amount_minor,
                currency=currency,
                status=PaymentStatus.SUCCEEDED,
                provider_status="verified manually",
                verified_by=actor,
                verified_at=timezone.now(),
            )
        else:
            attempt.status = PaymentStatus.SUCCEEDED
            attempt.verified_by = actor
            attempt.verified_at = timezone.now()
            attempt.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])

        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = timezone.now()
        invoice.verified_by = actor
        invoice.save(update_fields=["status", "paid_at", "verified_by", "updated_at"])

        audit_service.record(
            action="billing.payment_verified",
            actor=actor,
            organization_id=invoice.organization_id,
            resource_type="invoice",
            resource_id=str(invoice.pk),
            metadata={"reference": reference, "amount_minor": amount_minor},
        )
    return attempt


def _verify_signature(raw_body: bytes, signature: str) -> bool:
    secret = settings.PAYSTACK_WEBHOOK_SECRET or settings.PAYSTACK_SECRET_KEY
    if not secret:
        raise RuntimeError("Webhook secret is not configured.")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def process_webhook(
    *, raw_body: bytes, signature: str, provider: str = "paystack"
) -> tuple[ProviderWebhookEvent, bool]:
    """Verify, store once and apply a provider event. Returns (event, duplicate)."""
    if not settings.PAYMENTS_ENABLED:
        raise RuntimeError("Payments are disabled.")
    if not _verify_signature(raw_body, signature):
        raise PermissionFailure("Invalid webhook signature.")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("Malformed webhook payload.") from exc

    data = payload.get("data") or {}
    event_type = str(payload.get("event") or "")
    event_id = str(data.get("id") or data.get("reference") or "")
    if not event_id:
        raise ValidationFailure("Webhook is missing an event identifier.")
    payload_hash = hashlib.sha256(raw_body).hexdigest()

    with transaction.atomic():
        try:
            with transaction.atomic():
                event = ProviderWebhookEvent.objects.create(
                    provider=provider,
                    event_id=event_id,
                    event_type=event_type,
                    payload_hash=payload_hash,
                    payload=payload,
                    signature_valid=True,
                )
        except IntegrityError:
            return (
                ProviderWebhookEvent.objects.get(provider=provider, event_id=event_id),
                True,
            )

        outcome = "ignored"
        if event_type == "charge.success":
            outcome = _apply_charge_success(event, data)

        event.processed_at = timezone.now()
        event.outcome = outcome[:64]
        event.save(update_fields=["processed_at", "outcome"])
    return event, False


def _apply_charge_success(event: ProviderWebhookEvent, data: dict) -> str:
    reference = str(data.get("reference") or "")
    amount = data.get("amount")
    currency = data.get("currency")

    attempt = (
        PaymentAttempt.objects.select_for_update().filter(reference=reference).first()
    )
    if attempt is None:
        audit_service.record(
            action="billing.webhook_unknown_reference",
            actor=None,
            resource_type="payment_event",
            resource_id=str(event.pk),
            result=AuditEvent.Result.DENIED,
        )
        return "unknown_reference"

    if attempt.amount_minor != amount or attempt.currency != currency:
        audit_service.record(
            action="billing.webhook_amount_mismatch",
            actor=None,
            organization_id=attempt.invoice.organization_id,
            resource_type="payment_event",
            resource_id=str(event.pk),
            result=AuditEvent.Result.DENIED,
            metadata={"reference": reference},
        )
        return "amount_mismatch"

    attempt.status = PaymentStatus.SUCCEEDED
    attempt.provider_status = str(data.get("status") or "")[:64]
    attempt.save(update_fields=["status", "provider_status", "updated_at"])

    invoice = attempt.invoice
    if invoice.status == InvoiceStatus.ISSUED:
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = timezone.now()
        invoice.save(update_fields=["status", "paid_at", "updated_at"])

    _advance_task_after_payment(invoice, None, source="Provider webhook confirmed payment")

    audit_service.record(
        action="billing.webhook_paid",
        actor=None,
        organization_id=invoice.organization_id,
        resource_type="invoice",
        resource_id=str(invoice.pk),
        metadata={"reference": reference, "amount_minor": amount},
    )
    return "paid"
