"""Renewals, reminders, pausing, cancelling and downgrades (Terms 5.5–5.8).

Plans don't renew by themselves. Nothing is charged without the client paying:
- 3 days before a period ends, we issue the renewal invoice and remind the owners, with a pay link.
- Paying early carries on from the end of the current period, so no days are lost.
- If the period ends unpaid, service continues for 3 more days (the grace period), with a
  reminder at the start and on the last day.
- After that, the account is paused: it can be viewed and the renewal paid, nothing else
  (see apps.organizations.access). Paying restarts the plan from the day of payment.
- 30 days after pausing, the plan ends. The unpaid renewal is not a debt: it is voided,
  and the business can choose a plan again at any time.
- Cancelling (5.5.3): no more reminders; access runs to the end of the paid period.
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
    PaymentMethod,
    Plan,
    Subscription,
    SubscriptionStatus,
)

logger = logging.getLogger("ba.billing")

REMINDER_LEAD = timedelta(days=3)
GRACE = timedelta(days=3)
END_AFTER_PAUSE = timedelta(days=30)


# ---------- money and saved cards ----------

def _money(amount_minor: int, currency: str) -> str:
    symbol = "₦" if currency == "NGN" else "$"
    value = amount_minor / 100
    return f"{symbol}{value:,.0f}" if amount_minor % 100 == 0 else f"{symbol}{value:,.2f}"


def saved_card(organization_id) -> PaymentMethod | None:
    """A card saved before renewals became manual. It is never charged; the owner can remove it."""
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
    """The client won't renew: no more reminders. Access runs to the end of the paid period (Terms 5.5.3)."""
    with transaction.atomic():
        sub = _owner_subscription(organization, membership)
        if sub.cancel_at_period_end:
            return sub
        # After the period has ended (in the grace period), the next renewal run ends the plan:
        # nothing is owed for an unpaid renewal (Terms 5.6.6).
        sub.cancel_at_period_end = True
        sub.cancel_requested_at = timezone.now()
        sub.save(update_fields=["cancel_at_period_end", "cancel_requested_at", "updated_at"])
        _void_open_renewal_invoice(sub)
        _audit("billing.renewal_cancelled", membership.user, sub, ends=sub.period_end.isoformat())
    return sub


def undo_cancel(organization, membership) -> Subscription:
    from apps.organizations.models import Organization

    with transaction.atomic():
        sub = _owner_subscription(organization, membership)
        if Organization.objects.filter(pk=organization.pk, closure_requested_at__isnull=False).exists():
            raise ValidationFailure("Your account is set to close. Cancel the closure in Profile & security instead.")
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


def end_unrenewed(sub: Subscription) -> None:
    """A plan that ends without being renewed: its renewal invoice can no longer be paid."""
    _void_unpaid_renewals(sub)


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


def run(now=None) -> dict:
    """One pass over every plan that can be reminded, paused or ended. Safe to run repeatedly."""
    now = now or timezone.now()
    counts = {"reminded": 0, "grace": 0, "paused": 0, "ended": 0}
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
            updated = Subscription.objects.filter(pk=sub.pk, status=SubscriptionStatus.PAST_DUE).update(
                status=SubscriptionStatus.CANCELLED, updated_at=now
            )
            if updated:
                # Not renewing isn't a debt: the old renewal can't be paid any more.
                _void_unpaid_renewals(sub)
                _audit("billing.plan_ended_unpaid", None, sub)
                _tell_owners(sub, "Your plan has ended", (
                    "Your renewal wasn't paid within 30 days of your account being paused, so your plan has "
                    "ended. You can still sign in and see your records and deliverables. Nothing is owed. "
                    f"To start again, choose a plan: {_plan_link()}"))
                counts["ended"] += 1
        return

    # Cancelled: no reminders; the plan ends with the paid period.
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

    invoice = _renewal_invoice(sub)
    if invoice is not None and invoice.status == InvoiceStatus.PAID:
        return  # renewed; the payment moved the period on
    if now < sub.period_end - REMINDER_LEAD:
        return

    # From 3 days before the end there is always an invoice to pay (a downgrade
    # voids the old one, so a fresh one is issued at the new price).
    if invoice is None:
        with transaction.atomic():
            invoice = _create_renewal_invoice(sub)

    if now < sub.period_end:
        if sub.reminder_sent_for != sub.period_end:
            pause_on = sub.period_end + GRACE
            _tell_owners(sub, f"Your plan ends on {sub.period_end:%-d %b %Y}", (
                f"Your B&A Essentials {invoice.plan.name} plan ends on {sub.period_end:%-d %b %Y}. "
                f"Plans don't renew by themselves: pay {_money(invoice.amount_minor, invoice.currency)} "
                f"to keep it going for another {'year' if sub.interval == BillingInterval.YEAR else 'month'}: "
                f"{_plan_link()}\n\nIf it isn't paid by {pause_on:%-d %b %Y}, your account is paused until you pay. "
                "If you don't want to continue, you don't need to do anything."))
            Subscription.objects.filter(pk=sub.pk).update(reminder_sent_for=sub.period_end, updated_at=now)
            sub.reminder_sent_for = sub.period_end
            _audit("billing.renewal_reminded", None, sub, invoice=invoice.number)
            counts["reminded"] += 1
        return

    # The period has ended unpaid: 3 days' grace, then pause.
    if sub.grace_until is None:
        grace = sub.period_end + GRACE
        Subscription.objects.filter(pk=sub.pk).update(grace_until=grace, updated_at=now)
        sub.grace_until = grace
        _audit("billing.renewal_unpaid", None, sub, invoice=invoice.number, grace_until=grace.isoformat())
        _tell_owners(sub, "Your plan period has ended", (
            f"Your {invoice.plan.name} plan period ended on {sub.period_end:%-d %b %Y} and the renewal "
            f"({_money(invoice.amount_minor, invoice.currency)}) isn't paid yet. Your service continues until "
            f"{grace:%-d %b %Y}; after that your account is paused until you pay: {_plan_link()}"))
        counts["grace"] += 1
        return

    if now > sub.grace_until:
        updated = Subscription.objects.filter(pk=sub.pk, status=SubscriptionStatus.ACTIVE).update(
            status=SubscriptionStatus.PAST_DUE, paused_at=now, updated_at=now
        )
        if updated:
            _audit("billing.service_paused", None, sub, invoice=invoice.number)
            _tell_owners(sub, "Your account is paused", (
                "Your renewal is unpaid, so your account is paused. You can still sign in, see your records "
                "and download your deliverables, but nothing else can be done until you pay. "
                f"Pay to continue: {_plan_link()}"))
            counts["paused"] += 1
        return

    if not sub.grace_last_day_reminded and sub.grace_until - now <= timedelta(days=1):
        Subscription.objects.filter(pk=sub.pk).update(grace_last_day_reminded=True, updated_at=now)
        _tell_owners(sub, "Last day to pay your renewal", (
            f"Your account is paused after {sub.grace_until:%-d %b %Y} unless your renewal is paid. "
            f"Pay here: {_plan_link()}"))
