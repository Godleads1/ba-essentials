from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import logging
import uuid
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

from . import fees, paystack
from .models import (
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
    PaymentStatus,
    Plan,
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

    # Third-party fee invoices pay organisations, not for the work itself.
    work_invoices = Invoice.objects.filter(task=task, fee_request__isnull=True)
    paid = (
        work_invoices.filter(status=InvoiceStatus.PAID)
        .order_by("-issued_at")
        .first()
    )
    if paid is not None:
        return Eligibility(True, "Invoice paid.", paid)

    outstanding = (
        work_invoices.filter(status=InvoiceStatus.ISSUED)
        .order_by("-issued_at")
        .first()
    )
    if outstanding is not None:
        return Eligibility(False, "Payment is still outstanding.", outstanding)

    return Eligibility(False, "No plan allowance or paid invoice is on record.")


def is_fee_invoice(invoice: Invoice) -> bool:
    from .models import FeeRequest

    return FeeRequest.objects.filter(invoice_id=invoice.pk).exists()


def _advance_task_after_payment(invoice: Invoice, actor, *, source: str) -> None:
    """Move a task out of awaiting-payment once the invoice is genuinely paid."""
    if invoice.task_id is None or is_fee_invoice(invoice):
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

    with transaction.atomic():
        current = Invoice.objects.get(pk=invoice.pk)
        if current.status != InvoiceStatus.ISSUED:
            raise ValidationFailure("Only unpaid invoices can be marked as paid.")
        if amount_minor != current.amount_minor or currency != current.currency:
            raise ValidationFailure(
                "The amount or currency does not match the invoice exactly."
            )

        attempt = PaymentAttempt.objects.filter(reference=reference).first()
        if attempt is not None and attempt.invoice_id != current.pk:
            raise ValidationFailure("That reference is already used by another invoice.")
        now = timezone.now()
        if attempt is None:
            attempt = PaymentAttempt.objects.create(
                invoice=current,
                provider="manual",
                reference=reference,
                amount_minor=amount_minor,
                currency=currency,
                status=PaymentStatus.SUCCEEDED,
                provider_status="verified manually",
                verified_by=actor,
                verified_at=now,
            )
        else:
            attempt.status = PaymentStatus.SUCCEEDED
            attempt.verified_by = actor
            attempt.verified_at = now
            attempt.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])

        # Conditional write is the serialisation point. If another verification
        # paid this invoice first, this attempt (and its audit entry) rolls back.
        paid = Invoice.objects.filter(
            pk=current.pk, status=InvoiceStatus.ISSUED
        ).update(
            status=InvoiceStatus.PAID,
            paid_at=now,
            verified_by=actor,
            updated_at=now,
        )
        if not paid:
            raise ValidationFailure("Only unpaid invoices can be marked as paid.")
        fees.mark_paid(current)

        audit_service.record(
            action="billing.payment_verified",
            actor=actor,
            organization_id=current.organization_id,
            resource_type="invoice",
            resource_id=str(current.pk),
            metadata={"reference": reference, "amount_minor": amount_minor},
        )
    return attempt


class PaymentProviderUnavailable(Exception):
    """The payment provider couldn't be reached; the caller should retry later."""


def add_months(moment, months: int):
    """Same day and time `months` later, clamped to the end of shorter months."""
    month_index = moment.month - 1 + months
    year = moment.year + month_index // 12
    month = month_index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def start_plan_checkout(*, organization, membership, plan_slug: str, return_url: str) -> str:
    """Create the plan invoice and payment record, then start Paystack checkout.

    Price, currency and customer all come from the server. Returns the hosted
    checkout address. Nothing is activated here: only a verified payment does
    that (`process_webhook`).
    """
    from apps.organizations.models import MembershipRole

    if not settings.PAYMENTS_ENABLED:
        raise ValidationFailure("Payments aren't open yet.")
    if membership.role != MembershipRole.OWNER:
        raise PermissionFailure("Only the business owner can choose a plan.")
    plan = Plan.objects.filter(slug=plan_slug, is_active=True).first()
    if plan is None or plan.price_minor <= 0:
        raise ValidationFailure("That plan isn't available.")
    if plan.currency != settings.PAYSTACK_BASE_CURRENCY:
        raise ValidationFailure("That plan can't be paid with Paystack.")
    if active_subscription(organization) is not None:
        raise ValidationFailure("Your business already has an active plan.")

    user = membership.user
    with transaction.atomic():
        # Trying again reuses the open invoice for this plan and price, so an
        # abandoned checkout doesn't leave a trail of unpaid invoices. The old
        # payment reference stays valid: if it's paid late, it pays this invoice.
        invoice = (
            Invoice.objects.select_for_update()
            .filter(
                organization=organization,
                plan=plan,
                status=InvoiceStatus.ISSUED,
                amount_minor=plan.price_minor,
                currency=plan.currency,
            )
            .order_by("-issued_at")
            .first()
        )
        if invoice is None:
            number = next_value("invoice.number", start=1000)
            invoice = Invoice.objects.create(
                number=f"INV-{timezone.now():%Y}-{number}",
                organization=organization,
                plan=plan,
                amount_minor=plan.price_minor,
                currency=plan.currency,
                status=InvoiceStatus.ISSUED,
                due_at=timezone.now() + timedelta(days=1),
            )
    return _paystack_checkout(
        invoice, user, return_url=return_url, metadata={"invoice": invoice.number, "plan": plan.slug},
        void_on_failure=True,
    )


def can_pay_invoice(invoice: Invoice, membership) -> bool:
    """Only the business owner pays, as only the owner can accept quotes and choose plans."""
    from apps.organizations.models import MembershipRole

    return (
        membership is not None
        and membership.organization_id == invoice.organization_id
        and membership.role == MembershipRole.OWNER
    )


def start_invoice_checkout(*, invoice: Invoice, membership, return_url: str) -> str:
    """Start Paystack checkout for an issued request invoice (quote or third-party fees).

    The amount always comes from the stored invoice; the browser sends only which invoice.
    """
    if not settings.PAYMENTS_ENABLED:
        raise ValidationFailure("Payments aren't open yet.")
    if not can_pay_invoice(invoice, membership):
        raise PermissionFailure("You can't pay this invoice.")
    if invoice.plan_id is not None:
        raise ValidationFailure("Plan payments are made from the Plan page.")
    if invoice.status != InvoiceStatus.ISSUED:
        raise ValidationFailure("This invoice isn't open for payment.")
    if invoice.currency != settings.PAYSTACK_BASE_CURRENCY:
        raise ValidationFailure("This invoice can't be paid with Paystack.")
    metadata = {"invoice": invoice.number}
    if invoice.task_id:
        metadata["request"] = invoice.task.reference
    return _paystack_checkout(
        invoice, membership.user, return_url=return_url, metadata=metadata, void_on_failure=False
    )


def _paystack_checkout(
    invoice: Invoice, user, *, return_url: str, metadata: dict, void_on_failure: bool
) -> str:
    reference = f"BA-{uuid.uuid4().hex[:24].upper()}"
    with transaction.atomic():
        attempt = PaymentAttempt.objects.create(
            invoice=invoice,
            provider="paystack",
            reference=reference,
            amount_minor=invoice.amount_minor,
            currency=invoice.currency,
            status=PaymentStatus.INITIATED,
        )
        audit_service.record(
            action="billing.checkout_started",
            actor=user,
            organization_id=invoice.organization_id,
            resource_type="invoice",
            resource_id=str(invoice.pk),
            metadata={**metadata, "amount_minor": invoice.amount_minor, "reference": reference},
        )

    try:
        return paystack.initialize(
            email=user.email,
            amount_minor=attempt.amount_minor,
            currency=attempt.currency,
            reference=reference,
            callback_url=return_url,
            metadata=metadata,
        )
    except paystack.PaystackError as exc:
        PaymentAttempt.objects.filter(pk=attempt.pk).update(
            status=PaymentStatus.FAILED, provider_status="initialize failed", updated_at=timezone.now()
        )
        # A plan invoice exists only for this checkout, so it is voided if no other
        # attempt on it could still be paid. Request invoices stay open to retry.
        if void_on_failure and not invoice.payment_attempts.exclude(pk=attempt.pk).exclude(
            status=PaymentStatus.FAILED
        ).exists():
            Invoice.objects.filter(pk=invoice.pk, status=InvoiceStatus.ISSUED).update(
                status=InvoiceStatus.VOID, updated_at=timezone.now()
            )
        logger.warning("Paystack checkout could not start for %s: %s", reference, exc)
        raise ValidationFailure("We couldn't start the payment. Please try again.") from exc


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

    # Ask Paystack directly before applying a payment. Done before any row lock,
    # so a slow answer never holds the database. A known duplicate skips the call.
    confirmed = None
    if event_type == "charge.success" and not ProviderWebhookEvent.objects.filter(
        provider=provider, event_id=event_id
    ).exists():
        reference = str(data.get("reference") or "")
        try:
            confirmed = paystack.verify(reference) if reference else None
        except paystack.PaystackUnavailable as exc:
            # Nothing is stored, so Paystack's retry is processed in full.
            raise PaymentProviderUnavailable(str(exc)) from exc
        except paystack.PaystackError:
            confirmed = {}

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
            outcome = _apply_charge_success(event, data, confirmed)

        event.processed_at = timezone.now()
        event.outcome = outcome[:64]
        event.save(update_fields=["processed_at", "outcome"])
    return event, False


def _apply_charge_success(event: ProviderWebhookEvent, data: dict, confirmed: dict | None) -> str:
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

    # Paystack's own answer must agree with the webhook and with our record.
    confirmed = confirmed or {}
    if (
        confirmed.get("status") != "success"
        or confirmed.get("reference") != reference
        or confirmed.get("amount") != attempt.amount_minor
        or confirmed.get("currency") != attempt.currency
    ):
        audit_service.record(
            action="billing.webhook_not_confirmed",
            actor=None,
            organization_id=attempt.invoice.organization_id,
            resource_type="payment_event",
            resource_id=str(event.pk),
            result=AuditEvent.Result.DENIED,
            metadata={"reference": reference, "provider_status": str(confirmed.get("status") or "none")[:32]},
        )
        return "not_confirmed"

    attempt.status = PaymentStatus.SUCCEEDED
    attempt.provider_status = str(data.get("status") or "")[:64]
    attempt.save(update_fields=["status", "provider_status", "updated_at"])

    invoice = attempt.invoice
    # Conditional update: only the event that actually moves the invoice from
    # issued to paid may activate anything, however many events arrive.
    now = timezone.now()
    newly_paid = Invoice.objects.filter(pk=invoice.pk, status=InvoiceStatus.ISSUED).update(
        status=InvoiceStatus.PAID, paid_at=now, updated_at=now
    )
    invoice.refresh_from_db()

    _advance_task_after_payment(invoice, None, source="Provider webhook confirmed payment")
    if newly_paid:
        fees.mark_paid(invoice, confirmed=confirmed)
    if invoice.plan_id and newly_paid:
        _activate_subscription(invoice)

    audit_service.record(
        action="billing.webhook_paid",
        actor=None,
        organization_id=invoice.organization_id,
        resource_type="invoice",
        resource_id=str(invoice.pk),
        metadata={"reference": reference, "amount_minor": amount},
    )
    return "paid"


def _activate_subscription(invoice: Invoice) -> Subscription:
    """Start (or extend) the plan an invoice paid for. Runs inside the webhook's transaction."""
    now = timezone.now()
    subscription = (
        Subscription.objects.select_for_update()
        .filter(organization_id=invoice.organization_id)
        .first()
    )
    if subscription is None:
        subscription = Subscription.objects.create(
            organization_id=invoice.organization_id,
            plan_id=invoice.plan_id,
            period_start=now,
            period_end=add_months(now, 1),
            status=SubscriptionStatus.ACTIVE,
        )
    else:
        # A payment made while the current period is still running extends it,
        # so paying early never loses days.
        start = subscription.period_end if (
            subscription.status == SubscriptionStatus.ACTIVE and subscription.period_end > now
        ) else now
        subscription.plan_id = invoice.plan_id
        subscription.period_start = start
        subscription.period_end = add_months(start, 1)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.save(update_fields=["plan", "period_start", "period_end", "status", "updated_at"])
    audit_service.record(
        action="billing.subscription_activated",
        actor=None,
        organization_id=invoice.organization_id,
        resource_type="subscription",
        resource_id=str(subscription.pk),
        metadata={"invoice": invoice.number, "period_end": subscription.period_end.isoformat()},
    )
    return subscription
