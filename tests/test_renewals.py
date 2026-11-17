"""Manual renewal (Terms 5.5–5.8): nothing is charged automatically. A reminder and a pay link
3 days before the period ends, 5 business days' grace after it, then the account is paused (read-only
except paying); 30 days later the plan ends and nothing is owed. Cancelling and downgrades."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client, override_settings
from django.utils import timezone

from apps.billing import renewals
from apps.billing import services as billing
from apps.billing.models import (
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
    PaymentMethod,
    Plan,
    RenewalConsent,
    Subscription,
    SubscriptionStatus,
)
from apps.communications.models import Notification
from apps.core.dates import local
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.core.models import OutboxEvent

from .conftest import login_client

AUTH = {"authorization_code": "AUTH_synthetic123", "reusable": True, "last4": "4081",
        "brand": "visa", "exp_month": "12", "exp_year": "2030", "bank": "Test Bank", "country_code": "NG"}


@pytest.fixture
def plans(db, organization):
    call_command("seed_plans", stdout=open("/dev/null", "w"))  # noqa: SIM115
    organization.country = "NG"
    organization.save(update_fields=["country"])
    return {p.slug: p for p in Plan.objects.all()}


def _webhook(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(settings.PAYSTACK_WEBHOOK_SECRET.encode(), raw, hashlib.sha512).hexdigest()
    return Client().post("/webhooks/paystack/", raw, content_type="application/json",
                         HTTP_X_PAYSTACK_SIGNATURE=signature)


def _pay(attempt, fake_paystack, event_id, authorization=None):
    fake_paystack.confirm(attempt.reference, attempt.amount_minor, authorization=authorization)
    _webhook({"event": "charge.success", "data": {"id": event_id, "reference": attempt.reference,
              "amount": attempt.amount_minor, "currency": "NGN", "status": "success"}})
    attempt.refresh_from_db()
    return attempt


def _subscribe(organization, plan, *, ends_in=timedelta(days=20), interval="month"):
    now = timezone.now()
    return Subscription.objects.create(
        organization=organization, plan=plan, interval=interval,
        period_start=now - timedelta(days=10), period_end=now + ends_in, status=SubscriptionStatus.ACTIVE,
    )


def _old_saved_card(organization, owner):
    """A card saved before renewals became manual."""
    invoice = Invoice.objects.create(number=f"INV-T-{PaymentMethod.objects.count()}", organization=organization,
                                     amount_minor=100, currency="NGN")
    consent = RenewalConsent.objects.create(organization=organization, user=owner, invoice=invoice,
                                            wording="I agree", terms_version="2026-11")
    return PaymentMethod.objects.create(
        organization=organization, provider="paystack", token_encrypted="synthetic-encrypted-token",
        brand="Visa", last4="4081", exp_month=12, exp_year=2030, email="owner@example.test", consent=consent,
    )


def _charges(fake_paystack):
    return [c for c in fake_paystack.calls if c[1] == "/transaction/charge_authorization"]


def _pause(sub):
    Subscription.objects.filter(pk=sub.pk).update(
        status=SubscriptionStatus.PAST_DUE, paused_at=timezone.now(), period_end=timezone.now() - timedelta(days=4)
    )
    sub.refresh_from_db()
    return sub


# ---------- buying: no renewal box, no saved card ----------

@override_settings(PAYMENTS_ENABLED=True)
def test_a_plan_is_bought_without_any_renewal_agreement(plans, organization, owner):
    response = login_client(owner).post("/app/plan/checkout/starter/", {"interval": "month"})
    assert response.status_code == 302 and response["Location"].startswith("https://checkout.paystack.com/")
    assert Invoice.objects.filter(plan__slug="starter").exists()
    assert not RenewalConsent.objects.exists()


@override_settings(PAYMENTS_ENABLED=True)
def test_a_paid_plan_never_saves_the_card(plans, organization, owner, fake_paystack):
    login_client(owner).post("/app/plan/checkout/starter/", {"interval": "month"})
    _pay(PaymentAttempt.objects.get(), fake_paystack, 9001, authorization=AUTH)
    assert Subscription.objects.get(organization=organization).status == SubscriptionStatus.ACTIVE
    assert not PaymentMethod.objects.exists()


@override_settings(PAYMENTS_ENABLED=True)
def test_an_upgrade_needs_no_renewal_agreement(plans, organization, owner):
    _subscribe(organization, plans["starter"])
    url = billing.start_upgrade_checkout(organization=organization, membership=owner.memberships.get(),
                                         plan_slug="growth", return_url="https://x.test/")
    assert url.startswith("https://checkout.paystack.com/") and not RenewalConsent.objects.exists()


# ---------- 3 days before: reminder and pay link ----------

def test_reminder_and_invoice_three_days_before_and_only_once(plans, organization, owner):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(days=4))
    assert renewals.run()["reminded"] == 0 and not Invoice.objects.exists()
    later = timezone.now() + timedelta(days=1, hours=1)
    assert renewals.run(later)["reminded"] == 1
    assert renewals.run(later + timedelta(hours=1))["reminded"] == 0
    invoice = Invoice.objects.get(renews_period_end=sub.period_end)
    assert invoice.status == InvoiceStatus.ISSUED and invoice.amount_minor == plans["starter"].price_minor
    email = OutboxEvent.objects.get(topic=OutboxEvent.Topic.EMAIL)
    assert owner.email in email.payload["to"]
    body = email.payload["body"]
    assert "Plans don't renew by themselves" in body and "/app/plan/" in body
    assert f"{local(renewals.grace_end(sub.period_end)):%-d %b %Y}" in body  # when the pause would start
    assert Notification.objects.filter(user=owner, kind="billing").exists()


def test_yearly_plans_are_also_reminded_three_days_before(plans, organization, owner):
    _subscribe(organization, plans["starter"], ends_in=timedelta(days=4), interval="year")
    assert renewals.run()["reminded"] == 0
    assert renewals.run(timezone.now() + timedelta(days=1, hours=1))["reminded"] == 1
    assert "another year" in OutboxEvent.objects.get().payload["body"]


def test_nothing_is_ever_charged_even_with_an_old_saved_card(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    _old_saved_card(organization, owner)
    for step in (timedelta(0), timedelta(days=1), timedelta(days=3), timedelta(days=9)):
        renewals.run(timezone.now() + step)
    assert not _charges(fake_paystack)
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.PAST_DUE


def test_paying_early_carries_on_from_the_end_of_the_period(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(days=2))
    renewals.run()
    invoice = Invoice.objects.get(renews_period_end=sub.period_end)
    old_end = sub.period_end
    _pay(billing._create_attempt(invoice, owner, {}), fake_paystack, 9101)
    sub.refresh_from_db()
    assert sub.period_start == old_end and sub.period_end > old_end + timedelta(days=27)
    assert renewals.run(old_end + timedelta(days=1)) == {"reminded": 0, "grace": 0, "paused": 0, "ended": 0}


# ---------- after the end: 5 business days' grace, then paused ----------

def test_unpaid_end_gives_three_days_then_pauses_then_ends_owing_nothing(plans, organization, owner, make_task):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    renewals.run()
    after_end = sub.period_end + timedelta(minutes=1)
    assert renewals.run(after_end)["grace"] == 1
    sub.refresh_from_db()
    assert sub.grace_until == renewals.grace_end(sub.period_end)
    assert sub.grace_until - sub.period_end >= timedelta(days=5)  # never shorter than 5 days
    assert billing.in_paid_time(sub, sub.period_end + timedelta(days=2))
    assert Notification.objects.filter(title="Your plan period has ended").exists()

    renewals.run(sub.grace_until - timedelta(hours=12))
    assert Notification.objects.filter(title="Last day to pay your renewal").count() == 1

    paused_at = sub.grace_until + timedelta(minutes=1)
    assert renewals.run(paused_at)["paused"] == 1
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.PAST_DUE and sub.paused_at == paused_at
    assert "nothing else can be done until you pay" in OutboxEvent.objects.last().payload["body"]
    with pytest.raises(ValidationFailure, match="paused"):
        make_task(organization, owner)

    assert renewals.run(paused_at + timedelta(days=30, minutes=1))["ended"] == 1
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.CANCELLED
    # Not renewing isn't a debt: the renewal invoice can't be paid any more.
    assert not Invoice.objects.filter(organization=organization, status=InvoiceStatus.ISSUED).exists()
    assert "Nothing is owed" in OutboxEvent.objects.last().payload["body"]


def test_grace_counts_business_days_only():
    import datetime as dt

    lagos = timezone.get_current_timezone()
    friday = dt.datetime(2026, 11, 20, 18, 0, tzinfo=lagos)
    assert renewals.grace_end(friday) == dt.datetime(2026, 11, 27, 18, 0, tzinfo=lagos)  # next Friday
    wednesday = dt.datetime(2026, 11, 18, 9, 0, tzinfo=lagos)
    assert renewals.grace_end(wednesday) == dt.datetime(2026, 11, 25, 9, 0, tzinfo=lagos)


def test_a_missed_reminder_still_issues_the_invoice_at_the_end(plans, organization, owner):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(days=10))
    renewals.run(sub.period_end + timedelta(hours=1))
    assert Invoice.objects.filter(renews_period_end=sub.period_end, status=InvoiceStatus.ISSUED).exists()


def test_paying_during_grace_continues_from_the_old_end(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    renewals.run(sub.period_end + timedelta(minutes=1))
    sub.refresh_from_db()
    assert billing.in_paid_time(sub, sub.period_end + timedelta(days=1))
    invoice = Invoice.objects.get(renews_period_end=sub.period_end)
    old_end = sub.period_end
    _pay(billing._create_attempt(invoice, owner, {}), fake_paystack, 9201)
    sub.refresh_from_db()
    assert sub.period_start == old_end and sub.grace_until is None


def test_paying_while_paused_starts_the_period_that_day(plans, organization, owner, fake_paystack):
    sub = _pause(_subscribe(organization, plans["starter"]))
    invoice = renewals._create_renewal_invoice(sub)
    before = timezone.now()
    _pay(billing._create_attempt(invoice, owner, {}), fake_paystack, 9301)
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.ACTIVE and sub.period_start >= before and sub.paused_at is None


@override_settings(PAYMENTS_ENABLED=True)
def test_the_renewal_is_paid_by_link(plans, organization, owner):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(days=2))
    renewals.run()
    invoice = Invoice.objects.get(renews_period_end=sub.period_end)
    response = login_client(owner).post(f"/app/billing/invoices/{invoice.pk}/pay/")
    assert response.status_code == 302 and response["Location"].startswith("https://checkout.paystack.com/")


def test_a_bad_subscription_does_not_stop_the_others(plans, organization, owner, monkeypatch):
    _subscribe(organization, plans["starter"], ends_in=timedelta(days=2))
    monkeypatch.setattr(renewals, "_step", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert renewals.run()["reminded"] == 0  # logged, not raised


# ---------- a paused account is read-only, except paying ----------

@override_settings(PAYMENTS_ENABLED=True)
def test_a_paused_account_can_only_view_and_pay(plans, organization, owner, submitted_task):
    sub = _pause(_subscribe(organization, plans["starter"]))
    invoice = renewals._create_renewal_invoice(sub)
    c = login_client(owner)
    # Viewing still works, with a notice on every page.
    page = c.get(f"/app/tasks/{submitted_task.pk}/")
    assert page.status_code == 200 and "Your account is paused." in page.content.decode()
    # Other actions are refused and sent to the plan page.
    for url, data in [
        ("/app/tasks/new/", {"title": "A synthetic request"}),
        (f"/app/tasks/{submitted_task.pk}/cancel/", {"reason": "No longer needed"}),
        ("/app/team/invite/", {"email": "new.person@example.test", "role": "member"}),
        ("/app/plan/downgrade/", {"plan": "starter"}),
    ]:
        response = c.post(url, data)
        assert response.status_code == 302 and response["Location"] == "/app/plan/", url
    submitted_task.refresh_from_db()
    assert submitted_task.status != "cancelled"
    # Paying the renewal is allowed.
    response = c.post(f"/app/billing/invoices/{invoice.pk}/pay/")
    assert response["Location"].startswith("https://checkout.paystack.com/")


def test_an_active_account_is_not_blocked(plans, organization, owner, submitted_task):
    _subscribe(organization, plans["starter"])
    response = login_client(owner).post(f"/app/tasks/{submitted_task.pk}/cancel/", {"reason": "No longer needed"})
    assert response["Location"] != "/app/plan/"


# ---------- not renewing and downgrades ----------

def test_saying_you_wont_renew_stops_reminders_and_ends_at_the_end(plans, organization, owner):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(days=2))
    login_client(owner).post("/app/plan/cancel/")
    sub.refresh_from_db()
    assert sub.cancel_at_period_end
    assert renewals.run()["reminded"] == 0
    assert billing.active_subscription(organization) == sub
    assert renewals.run(sub.period_end + timedelta(minutes=1))["ended"] == 1
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.CANCELLED
    assert "ended as you asked" in OutboxEvent.objects.last().payload["body"]


def test_not_renewing_during_grace_ends_the_plan_owing_nothing(plans, organization, owner):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=1))
    renewals.run(sub.period_end + timedelta(minutes=1))
    with pytest.MonkeyPatch.context() as mp:
        later = sub.period_end + timedelta(hours=1)
        mp.setattr(timezone, "now", lambda: later)
        renewals.cancel_renewal(organization, owner.memberships.get())
    assert renewals.run(sub.period_end + timedelta(hours=2))["ended"] == 1
    assert not Invoice.objects.filter(organization=organization, status=InvoiceStatus.ISSUED).exists()


def test_undo_and_only_the_owner_can_say_they_wont_renew(plans, organization, owner, member):
    sub = _subscribe(organization, plans["starter"])
    with pytest.raises(PermissionFailure):
        renewals.cancel_renewal(organization, member.memberships.get())
    renewals.cancel_renewal(organization, owner.memberships.get())
    renewals.undo_cancel(organization, owner.memberships.get())
    sub.refresh_from_db()
    assert not sub.cancel_at_period_end


def test_not_renewing_voids_a_prepared_renewal_invoice(plans, organization, owner):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(days=2))
    renewals.run()
    invoice = Invoice.objects.get(renews_period_end=sub.period_end)
    renewals.cancel_renewal(organization, owner.memberships.get())
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.VOID


def test_downgrade_takes_effect_at_renewal(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["growth"], ends_in=timedelta(days=2))
    renewals.run()  # an invoice at the Growth price is issued first
    with pytest.raises(ValidationFailure):
        renewals.schedule_downgrade(organization, owner.memberships.get(), "dedicated")
    login_client(owner).post("/app/plan/downgrade/", {"plan": "starter"})
    sub.refresh_from_db()
    assert sub.next_plan.slug == "starter" and sub.plan.slug == "growth"
    renewals.run()  # the old invoice was voided; a new one at the Starter price is issued
    invoice = Invoice.objects.get(renews_period_end=sub.period_end, status=InvoiceStatus.ISSUED)
    assert invoice.plan.slug == "starter" and invoice.amount_minor == plans["starter"].price_minor
    _pay(billing._create_attempt(invoice, owner, {}), fake_paystack, 9401)
    sub.refresh_from_db()
    assert sub.plan.slug == "starter" and sub.next_plan is None


def test_an_old_saved_card_can_be_removed(plans, organization, owner):
    _subscribe(organization, plans["starter"])
    _old_saved_card(organization, owner)
    login_client(owner).post("/app/plan/card/remove/")
    assert renewals.saved_card(organization.pk) is None


# ---------- the plan page ----------

@override_settings(PAYMENTS_ENABLED=True)
def test_plan_page_explains_manual_renewal(plans, organization, owner):
    sub = _subscribe(organization, plans["growth"])
    body = login_client(owner).get("/app/plan/").content.decode()
    assert "It doesn't renew by itself" in body and f"{local(sub.period_end - timedelta(days=3)):%-d %b}" in body
    assert "I won't renew" in body and "Move down at renewal" in body and 'name="renews"' not in body


@override_settings(PAYMENTS_ENABLED=True)
def test_plan_page_offers_renew_now_once_the_invoice_is_out(plans, organization, owner):
    _subscribe(organization, plans["growth"], ends_in=timedelta(days=2))
    renewals.run()
    body = login_client(owner).get("/app/plan/").content.decode()
    assert "Renew now" in body and "paying early loses nothing" in body


@override_settings(PAYMENTS_ENABLED=True)
def test_plan_page_when_paused(plans, organization, owner):
    sub = _pause(_subscribe(organization, plans["starter"]))
    renewals._create_renewal_invoice(sub)
    body = login_client(owner).get("/app/plan/").content.decode()
    assert "Your account is paused" in body and "to resume" in body and "nothing is owed after that" in body


# ---------- Terms 5.6.4: paused work ----------

def test_work_in_progress_pauses_too(plans, organization, owner, submitted_task, ops_user, quote_approver):
    from apps.tasks.transitions import Action, available_actions, transition

    task = transition(task=submitted_task, action=Action.TRIAGE, actor=ops_user)
    _pause(_subscribe(organization, plans["starter"]))
    assert Action.SCOPE.value not in available_actions(task, quote_approver)
    with pytest.raises(ValidationFailure, match="paused"):
        transition(task=task, action=Action.SCOPE, actor=quote_approver)
    # Holding and talking to the client still work; nothing is cancelled.
    held = transition(task=task, action=Action.HOLD, actor=ops_user, reason="Client renewal unpaid")
    assert held.status == "on_hold"


def test_paused_notices_on_task_pages(plans, organization, owner, submitted_task, ops_user):
    _pause(_subscribe(organization, plans["starter"]))
    assert "work on this request is paused" in login_client(owner).get(f"/app/tasks/{submitted_task.pk}/").content.decode()
    assert "service is paused: renewal unpaid" in login_client(ops_user).get(f"/staff/tasks/{submitted_task.pk}/").content.decode()


@override_settings(PAYMENTS_ENABLED=True)
def test_after_an_unpaid_plan_ends_a_new_plan_can_be_chosen(plans, organization, owner):
    sub = _pause(_subscribe(organization, plans["starter"]))
    renewals._create_renewal_invoice(sub)
    renewals.run(timezone.now() + timedelta(days=31))
    url = billing.start_plan_checkout(organization=organization, membership=owner.memberships.get(),
                                      plan_slug="growth", return_url="https://x.test/")
    assert url.startswith("https://checkout.paystack.com/")


# ---------- dates are Lagos dates (failed on main at 23:05 UTC, 17 Nov 2026) ----------

def test_reminder_email_uses_the_lagos_date_late_at_night_utc(plans, organization, owner):
    from datetime import UTC, datetime

    sub = _subscribe(organization, plans["starter"])
    # 23:30 UTC on 1 Dec is 00:30 on 2 Dec in Lagos: the customer's plan ends on 2 Dec.
    sub.period_end = datetime(2026, 12, 1, 23, 30, tzinfo=UTC)
    sub.save()
    renewals.run(datetime(2026, 11, 29, 12, 0, tzinfo=UTC))
    email = OutboxEvent.objects.get(topic=OutboxEvent.Topic.EMAIL).payload
    assert "ends on 2 Dec 2026" in email["subject"] and "1 Dec" not in email["body"]


def test_local_leaves_dates_and_missing_values_alone():
    from datetime import UTC, date, datetime

    assert local(datetime(2026, 12, 31, 23, 30, tzinfo=UTC)).year == 2027  # invoice numbers use the Lagos year
    assert local(date(2026, 12, 1)) == date(2026, 12, 1) and local(None) is None
