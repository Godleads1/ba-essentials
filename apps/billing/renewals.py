"""Automatic renewal, reminders, payment problems, cancelling and downgrades (Terms 5.4–5.8).

- Consent (5.5.2–5.5.3): a plan is bought only after the client ticks the renewal box.
  We record the exact wording, the terms version, when, and the provider reference
  for the card permission. The card itself stays with Paystack or Flutterwave; we keep
  an encrypted token and the brand, last four digits and expiry.
- Reminders (5.4): at least 5 days before a monthly renewal, 30 days before a yearly one.
- Renewal: the saved card is charged about a day before the period ends, at the price
  for the business's region then. Only a payment the provider confirms (webhook +
  verify) renews anything. With no saved card, the reminder carries a pay link.
- Payment problems (5.6): told the same day; a grace period of five business days in
  which service continues; up to three attempts; reminders on the first and last
  day; then service pauses. Thirty days after pausing, the plan ends.
- Cancelling (5.5.5): stops the next renewal; access runs to the end of the paid period.
- Downgrades (5.7): take effect at the next renewal.

`run()` is called by the `run_renewals` management command once or more a day.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit_service
from apps.core.exceptions import PermissionFailure, ValidationFailure

from .models import (
    BillingInterval,
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
    PaymentMethod,
    PaymentStatus,
    Plan,
    RenewalConsent,
    Subscription,
    SubscriptionStatus,
)

logger = logging.getLogger("ba.billing")

TERMS_VERSION = "2026-11"
CHARGE_AHEAD = timedelta(days=1)
REMINDER_LEAD = {BillingInterval.MONTH: timedelta(days=5), BillingInterval.YEAR: timedelta(days=30)}
GRACE_BUSINESS_DAYS = 5
MAX_ATTEMPTS = 3
RETRY_GAP = timedelta(days=2)
END_AFTER_PAUSE = timedelta(days=30)


# ---------- wording, consent and saved cards ----------

def _fernet():
    """Card tokens are encrypted at rest with their own key, derived from the secret key.
    Rotating the secret key makes saved tokens unreadable; renewals then fall back to a pay link."""
    import base64
    import hashlib

    from cryptography.fernet import Fernet
    from django.conf import settings

    digest = hashlib.sha256(f"{settings.SECRET_KEY}:ba-essentials-card-token-v1".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode("ascii")


def decrypt_secret(value: str) -> str | None:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value.encode("ascii")).decode()
    except (InvalidToken, ValueError):
        return None

def _money(amount_minor: int, currency: str) -> str:
    symbol = "₦" if currency == "NGN" else "$"
    value = amount_minor / 100
    return f"{symbol}{value:,.0f}" if amount_minor % 100 == 0 else f"{symbol}{value:,.2f}"


def consent_wording(plan: Plan, amount_minor: int, currency: str, interval: str, first_renewal) -> str:
    every = "year" if interval == BillingInterval.YEAR else "month"
    return (
        f"I agree that my {plan.name} plan renews automatically every {every} at "
        f"{_money(amount_minor, currency)} until I cancel. My first renewal is on "
        f"{first_renewal:%-d %b %Y}. I can cancel at any time before a renewal date."
    )


def record_consent(invoice: Invoice, user, wording: str) -> RenewalConsent:
    """Inside the checkout transaction, before the client is sent to pay."""
    consent, _ = RenewalConsent.objects.get_or_create(
        invoice=invoice,
        defaults={
            "organization_id": invoice.organization_id,
            "user": user,
            "wording": wording,
            "terms_version": TERMS_VERSION,
        },
    )
    return consent


def save_payment_method(invoice: Invoice, attempt: PaymentAttempt, confirmed: dict | None) -> PaymentMethod | None:
    """After a confirmed plan payment the client agreed to renew: keep the card permission."""
    consent = RenewalConsent.objects.filter(invoice=invoice).first()
    card = (confirmed or {}).get("card") or {}
    if consent is None or not card.get("token") or not card.get("reusable"):
        return None
    consent.provider_reference = attempt.reference
    consent.save(update_fields=["provider_reference"])
    now = timezone.now()
    PaymentMethod.objects.filter(organization_id=invoice.organization_id, removed_at__isnull=True).update(
        removed_at=now, updated_at=now
    )
    method = PaymentMethod.objects.create(
        organization_id=invoice.organization_id,
        provider=attempt.provider,
        token_encrypted=encrypt_secret(card["token"]),
        brand=card.get("brand") or "",
        last4=card.get("last4") or "",
        exp_month=card.get("exp_month"),
        exp_year=card.get("exp_year"),
        bank=card.get("bank") or "",
        email=str((confirmed or {}).get("email") or "")[:254],
        consent=consent,
    )
    audit_service.record(
        action="billing.card_saved",
        actor=None,
        organization_id=invoice.organization_id,
        resource_type="payment_method",
        resource_id=str(method.pk),
        metadata={"provider": attempt.provider, "last4": method.last4, "invoice": invoice.number},
    )
    return method


def owed_renewal(organization_id) -> Invoice | None:
    """An unpaid renewal from a plan that ended unpaid. It stays owed (Terms 5.6.6)."""
    return (
        Invoice.objects.filter(
            organization_id=organization_id, status=InvoiceStatus.ISSUED, renews_period_end__isnull=False,
        )
        .exclude(organization__subscription__status__in=(SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE))
        .order_by("-issued_at")
        .first()
    )


def saved_card(organization_id) -> PaymentMethod | None:
    return PaymentMethod.objects.filter(organization_id=organization_id, removed_at__isnull=True).first()


# ---------- client choices ----------

def _owner_subscription(organization, membership) -> Subscription:
    from apps.organizations.models import MembershipRole

    if membership.role != MembershipRole.OWNER:
        raise PermissionFailure("Only the business owner can change the plan.")
    subscription = (
        Subscription.objects.select_for_update()
        .filter(organization=organization, status=SubscriptionStatus.ACTIVE)
        .select_related("plan")
        .first()
    )
    if subscription is None:
        raise ValidationFailure("There's no active plan to change.")
    return subscription


def cancel_renewal(organization, membership) -> Subscription:
    """Stop the next renewal. Access runs to the end of the paid period (Terms 5.5.5)."""
    with transaction.atomic():
        sub = _owner_subscription(organization, membership)
        if sub.cancel_at_period_end:
            return sub
        if timezone.now() > sub.period_end:
            # The renewal date has passed and the renewal is unpaid: cancelling now
            # can't wipe out what's owed for the grace period (Terms 5.5.5, 5.6).
            raise ValidationFailure(
                "Your renewal date has passed. Pay the renewal first, or contact support to end your plan."
            )
        sub.cancel_at_period_end = True
        sub.cancel_requested_at = timezone.now()
        sub.save(update_fields=["cancel_at_period_end", "cancel_requested_at", "updated_at"])
        _void_open_renewal_invoice(sub)
        _audit("billing.renewal_cancelled", membership.user, sub, ends=sub.period_end.isoformat())
    return sub


def undo_cancel(organization, membership) -> Subscription:
    with transaction.atomic():
        sub = _owner_subscription(organization, membership)
        if timezone.now() > sub.period_end:
            raise ValidationFailure("This plan has already ended. Choose a plan to start again.")
        sub.cancel_at_period_end = False
        sub.cancel_requested_at = None
        sub.save(update_fields=["cancel_at_period_end", "cancel_requested_at", "updated_at"])
        _audit("billing.renewal_resumed", membership.user, sub)
    return sub


def schedule_downgrade(organization, membership, plan_slug: str) -> Subscription:
    """A smaller plan starts at the next renewal (Terms 5.7)."""
    with transaction.atomic():
        sub = _owner_subscription(organization, membership)
        target = Plan.objects.filter(slug=plan_slug, is_active=True).first()
        if target is None or target.rank >= sub.plan.rank:
            raise ValidationFailure("Choose a smaller plan. To move up, use Upgrade.")
        sub.next_plan = target
        sub.save(update_fields=["next_plan", "updated_at"])
        _void_open_renewal_invoice(sub)
        _audit("billing.downgrade_scheduled", membership.user, sub, to=target.slug, starts=sub.period_end.isoformat())
    return sub


def undo_downgrade(organization, membership) -> Subscription:
    with transaction.atomic():
        sub = _owner_subscription(organization, membership)
        sub.next_plan = None
        sub.save(update_fields=["next_plan", "updated_at"])
        _void_open_renewal_invoice(sub)
        _audit("billing.downgrade_cancelled", membership.user, sub)
    return sub


def remove_card(organization, membership) -> None:
    """The client withdraws the saved-card permission. Renewals then come with a pay link."""
    from apps.organizations.models import MembershipRole

    if membership.role != MembershipRole.OWNER:
        raise PermissionFailure("Only the business owner can remove the saved card.")
    now = timezone.now()
    with transaction.atomic():
        methods = list(
            PaymentMethod.objects.select_for_update().filter(organization=organization, removed_at__isnull=True)
        )
        for method in methods:
            method.removed_at = now
            method.save(update_fields=["removed_at", "updated_at"])
            audit_service.record(
                action="billing.card_removed",
                actor=membership.user,
                organization_id=organization.pk,
                resource_type="payment_method",
                resource_id=str(method.pk),
            )


def _void_open_renewal_invoice(sub: Subscription) -> None:
    """A change before renewal replaces the prepared renewal invoice, so the old amount can't be paid."""
    Invoice.objects.filter(
        organization_id=sub.organization_id, status=InvoiceStatus.ISSUED, renews_period_end=sub.period_end
    ).update(status=InvoiceStatus.VOID, updated_at=timezone.now())


def _void_unpaid_renewals(sub: Subscription) -> None:
    """When a plan ends, its old renewal price can no longer be paid; the client chooses a plan afresh."""
    Invoice.objects.filter(
        organization_id=sub.organization_id, status=InvoiceStatus.ISSUED, renews_period_end__isnull=False
    ).update(status=InvoiceStatus.VOID, updated_at=timezone.now())


def _audit(action, actor, sub, **metadata):
    audit_service.record(
        action=action,
        actor=actor,
        organization_id=sub.organization_id,
        resource_type="subscription",
        resource_id=str(sub.pk),
        metadata=metadata,
    )


# ---------- telling the client ----------

def _tell_owners(sub: Subscription, title: str, body: str) -> None:
    """In-app notification and an email to each owner, the same day (Terms 5.6.2)."""
    from apps.communications import services as communications
    from apps.communications.models import NotificationKind
    from apps.core.models import OutboxEvent
    from apps.organizations.models import Membership, MembershipRole

    owners = [
        m.user for m in Membership.objects.filter(
            organization_id=sub.organization_id, role=MembershipRole.OWNER
        ).select_related("user")
    ]
    communications.notify(
        owners, organization_id=sub.organization_id, kind=NotificationKind.BILLING, title=title
    )
    emails = [u.email for u in owners if u.is_active and u.email]
    if emails:
        OutboxEvent.objects.create(
            topic=OutboxEvent.Topic.EMAIL,
            payload={"to": emails, "subject": f"B&A Essentials: {title}", "body": body},
        )


def _plan_link() -> str:
    from django.conf import settings

    return f"{settings.APP_BASE_URL.rstrip('/')}/app/plan/"


# ---------- the renewal run ----------

def add_business_days(start, days: int):
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def renewal_price(sub: Subscription):
    from . import pricing

    plan = sub.next_plan or sub.plan
    return plan, pricing.plan_price(plan, sub.organization.region, sub.interval)


def _renewal_invoice(sub: Subscription) -> Invoice | None:
    return (
        Invoice.objects.filter(organization_id=sub.organization_id, renews_period_end=sub.period_end)
        .exclude(status=InvoiceStatus.VOID)
        .order_by("-issued_at")
        .first()
    )


def _create_renewal_invoice(sub: Subscription) -> Invoice:
    from apps.core.sequences import next_value

    plan, price = renewal_price(sub)
    number = next_value("invoice.number", start=1000)
    return Invoice.objects.create(
        number=f"INV-{timezone.now():%Y}-{number}",
        organization_id=sub.organization_id,
        plan=plan,
        amount_minor=price.amount_minor,
        currency=price.currency,
        interval=sub.interval,
        fx_ngn_per_usd=price.rate,
        renews_period_end=sub.period_end,
        status=InvoiceStatus.ISSUED,
        due_at=sub.period_end,
    )


def _charge(sub: Subscription, invoice: Invoice) -> str:
    """Try the saved card once. Returns 'sent' (wait for the provider to confirm), 'failed' or 'no_card'."""
    from . import flutterwave, paystack
    from .services import _create_attempt

    method = saved_card(sub.organization_id)
    from . import pricing

    if method is None or method.provider != pricing.provider_for(invoice.currency):
        return "no_card"
    token = decrypt_secret(method.token_encrypted)
    if not token:
        logger.warning("Saved card for %s can't be read; sending a pay link instead", invoice.number)
        return "no_card"
    with transaction.atomic():
        attempt = _create_attempt(invoice, None, {"invoice": invoice.number, "renewal": "1"})
    client = paystack if method.provider == "paystack" else flutterwave
    error = paystack.PaystackError if method.provider == "paystack" else flutterwave.FlutterwaveError
    try:
        status = client.charge_saved_card(
            token=token,
            email=method.email or _owner_email(sub),
            amount_minor=attempt.amount_minor,
            currency=attempt.currency,
            reference=attempt.reference,
            metadata={"invoice": invoice.number, "renewal": "1"},
        )
    except (error, ValidationFailure) as exc:
        logger.warning("Renewal charge for %s could not be made: %s", invoice.number, exc)
        status = "failed"
    if status != "success":
        PaymentAttempt.objects.filter(pk=attempt.pk, status=PaymentStatus.INITIATED).update(
            status=PaymentStatus.FAILED, provider_status=f"renewal {status}"[:64], updated_at=timezone.now()
        )
        return "failed"
    return "sent"


def _owner_email(sub: Subscription) -> str:
    from apps.organizations.models import Membership, MembershipRole

    owner = Membership.objects.filter(
        organization_id=sub.organization_id, role=MembershipRole.OWNER
    ).select_related("user").order_by("created_at").first()
    return owner.user.email if owner else ""


def run(now=None) -> dict:
    """One pass over every plan that can renew, pause or end. Safe to run repeatedly."""
    now = now or timezone.now()
    counts = {"reminded": 0, "charged": 0, "grace": 0, "retried": 0, "paused": 0, "ended": 0}
    subs = Subscription.objects.filter(
        status__in=(SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE)
    ).select_related("plan", "organization", "next_plan")
    for sub in subs:
        try:
            _step(sub, now, counts)
        except Exception:  # one business's problem must not stop the others
            logger.exception("Renewal step failed for subscription %s", sub.pk)
    return counts


def _step(sub: Subscription, now, counts: dict) -> None:
    if sub.status == SubscriptionStatus.PAST_DUE:
        if sub.paused_at and now >= sub.paused_at + END_AFTER_PAUSE:
            Subscription.objects.filter(pk=sub.pk, status=SubscriptionStatus.PAST_DUE).update(
                status=SubscriptionStatus.CANCELLED, updated_at=now
            )
            # The unpaid renewal stays owed (Terms 5.6.6); paying it restarts the plan from that day.
            _audit("billing.plan_ended_unpaid", None, sub)
            _tell_owners(sub, "Your plan has ended", (
                "Your renewal is still unpaid 30 days after your service was paused, so your plan has ended. "
                "The renewal amount is still owed. You can still see your records. Paying it restarts your "
                "plan from the day you pay: "
                f"at any time: {_plan_link()}"))
            counts["ended"] += 1
        return

    # Cancelled: nothing more is charged; the plan ends with the paid period.
    if sub.cancel_at_period_end:
        if now >= sub.period_end:
            Subscription.objects.filter(pk=sub.pk).update(status=SubscriptionStatus.CANCELLED, updated_at=now)
            _void_unpaid_renewals(sub)
            _audit("billing.plan_ended_cancelled", None, sub)
            _tell_owners(sub, "Your plan has ended", (
                f"Your {sub.plan.name} plan has ended as you asked. Nothing more will be charged. "
                f"You can choose a plan again at any time: {_plan_link()}"))
            counts["ended"] += 1
        return

    # Reminder before renewal (5.4): once per period.
    lead = REMINDER_LEAD.get(sub.interval, REMINDER_LEAD[BillingInterval.MONTH])
    if sub.reminder_sent_for != sub.period_end and sub.period_end - now <= lead and now < sub.period_end:
        plan, price = renewal_price(sub)
        card = saved_card(sub.organization_id)
        how = f"We'll charge your {card.label}." if card else f"Pay here when you're ready: {_plan_link()}"
        _tell_owners(sub, f"Your plan renews on {sub.period_end:%-d %b %Y}", (
            f"Your B&A Essentials {plan.name} plan renews on {sub.period_end:%-d %b %Y} for "
            f"{_money(price.amount_minor, price.currency)}. {how} No action is needed to continue. "
            f"To cancel, go to {_plan_link()} or email support@bandaessentials.com."))
        Subscription.objects.filter(pk=sub.pk).update(reminder_sent_for=sub.period_end, updated_at=now)
        sub.reminder_sent_for = sub.period_end
        counts["reminded"] += 1

    invoice = _renewal_invoice(sub)
    if invoice is not None and invoice.status == InvoiceStatus.PAID:
        return  # renewed; the webhook moved the period on

    # First charge, about a day before the period ends.
    if sub.grace_until is None and now >= sub.period_end - CHARGE_AHEAD:
        if invoice is None:
            with transaction.atomic():
                invoice = _create_renewal_invoice(sub)
            outcome = _charge(sub, invoice)
            Subscription.objects.filter(pk=sub.pk).update(
                renewal_attempts=1 if outcome != "no_card" else 0, last_renewal_attempt_at=now, updated_at=now
            )
            sub.renewal_attempts = 1 if outcome != "no_card" else 0
            sub.last_renewal_attempt_at = now
            counts["charged"] += outcome == "sent"
            if outcome == "sent":
                return
        elif sub.renewal_attempts and now < sub.period_end:
            return  # a charge is out; wait for the provider
        # Not paid (declined, no card, or still unconfirmed at period end): grace starts (5.6.3).
        if _awaiting_provider(invoice, now):
            return
        if now >= sub.period_end or (sub.renewal_attempts and _last_attempt_failed(invoice)):
            grace = add_business_days(max(now, sub.period_end), GRACE_BUSINESS_DAYS)
            Subscription.objects.filter(pk=sub.pk).update(grace_until=grace, updated_at=now)
            sub.grace_until = grace
            _audit("billing.renewal_failed", None, sub, invoice=invoice.number, grace_until=grace.isoformat())
            _tell_owners(sub, "Your renewal payment didn't go through", (
                f"We couldn't take {_money(invoice.amount_minor, invoice.currency)} for your {invoice.plan.name} "
                f"plan. Your service continues until {grace:%-d %b %Y}. Please pay or update your card "
                f"before then: {_plan_link()}"))
            counts["grace"] += 1
        return

    if sub.grace_until is None:
        return

    # In the grace period: retries, last-day reminder, then pause.
    if now > sub.grace_until:
        Subscription.objects.filter(pk=sub.pk, status=SubscriptionStatus.ACTIVE).update(
            status=SubscriptionStatus.PAST_DUE, paused_at=now, updated_at=now
        )
        _audit("billing.service_paused", None, sub, invoice=invoice.number if invoice else "")
        _tell_owners(sub, "Your service is paused", (
            "Your renewal is unpaid, so your service is paused. You can still see your records and "
            f"deliverables, but new requests wait until you pay. Pay to resume: {_plan_link()}"))
        counts["paused"] += 1
        return
    if invoice is not None and sub.renewal_attempts < MAX_ATTEMPTS and saved_card(sub.organization_id):
        if not sub.last_renewal_attempt_at or now - sub.last_renewal_attempt_at >= RETRY_GAP:
            outcome = _charge(sub, invoice)
            Subscription.objects.filter(pk=sub.pk).update(
                renewal_attempts=sub.renewal_attempts + 1, last_renewal_attempt_at=now, updated_at=now
            )
            counts["retried"] += 1
            if outcome == "sent":
                return
    if not sub.grace_last_day_reminded and sub.grace_until - now <= timedelta(days=1):
        Subscription.objects.filter(pk=sub.pk).update(grace_last_day_reminded=True, updated_at=now)
        _tell_owners(sub, "Last day to pay your renewal", (
            f"Your service pauses after {sub.grace_until:%-d %b %Y} unless your renewal is paid. "
            f"Pay here: {_plan_link()}"))


def _last_attempt_failed(invoice: Invoice | None) -> bool:
    if invoice is None:
        return False
    last = invoice.payment_attempts.order_by("-created_at").first()
    return last is not None and last.status == PaymentStatus.FAILED


def _awaiting_provider(invoice: Invoice | None, now) -> bool:
    """A charge the provider accepted but hasn't confirmed yet gets a day before we call it failed."""
    if invoice is None:
        return False
    last = invoice.payment_attempts.order_by("-created_at").first()
    return bool(last and last.status == PaymentStatus.INITIATED and now - last.created_at < CHARGE_AHEAD)
