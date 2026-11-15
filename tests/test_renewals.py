"""Automatic renewal and cancelling (Terms 5.4–5.8): consent before the first payment,
reminders, the saved-card charge, grace, pause and ending, cancel and downgrade."""
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
    PaymentStatus,
    Plan,
    RenewalConsent,
    Subscription,
    SubscriptionStatus,
)
from apps.communications.models import Notification
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.core.models import OutboxEvent

from .conftest import login_client

AUTH = {"authorization_code": "AUTH_synthetic123", "reusable": True, "last4": "4081",
        "brand": "visa", "exp_month": "12", "exp_year": "2030", "bank": "Test Bank", "country_code": "NG"}


@pytest.fixture
def plans(db, organization):
    call_command("seed_plans", stdout=open("/dev/null", "w"))
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


def _save_card(organization, owner):
    invoice = Invoice.objects.create(number=f"INV-T-{PaymentMethod.objects.count()}", organization=organization,
                                     amount_minor=100, currency="NGN")
    consent = RenewalConsent.objects.create(organization=organization, user=owner, invoice=invoice,
                                            wording="I agree", terms_version=renewals.TERMS_VERSION)
    return PaymentMethod.objects.create(
        organization=organization, provider="paystack", token_encrypted=renewals.encrypt_secret("AUTH_synthetic123"),
        brand="Visa", last4="4081", exp_month=12, exp_year=2030, email="owner@example.test", consent=consent,
    )


def _charges(fake_paystack):
    return [c for c in fake_paystack.calls if c[1] == "/transaction/charge_authorization"]


# ---------- consent and the saved card ----------

@override_settings(PAYMENTS_ENABLED=True)
def test_a_plan_cannot_be_bought_without_ticking_renewal(plans, organization, owner):
    c = login_client(owner)
    c.post("/app/plan/checkout/starter/", {"interval": "month"})
    assert not Invoice.objects.exists()
    c.post("/app/plan/checkout/starter/", {"interval": "month", "renews": "on"})
    consent = RenewalConsent.objects.get()
    assert consent.user == owner and consent.terms_version == renewals.TERMS_VERSION
    assert "renews automatically every month at ₦" in consent.wording and "until I cancel" in consent.wording


@override_settings(PAYMENTS_ENABLED=True)
def test_a_confirmed_payment_saves_the_card_encrypted(plans, organization, owner, fake_paystack):
    login_client(owner).post("/app/plan/checkout/starter/", {"interval": "month", "renews": "on"})
    attempt = PaymentAttempt.objects.get()
    _pay(attempt, fake_paystack, 9001, authorization=AUTH)
    method = PaymentMethod.objects.get()
    assert method.label.startswith("Visa ending 4081") and method.exp_year == 2030
    assert "AUTH_synthetic123" not in method.token_encrypted
    assert renewals.decrypt_secret(method.token_encrypted) == "AUTH_synthetic123"
    assert RenewalConsent.objects.get().provider_reference == attempt.reference


@override_settings(PAYMENTS_ENABLED=True)
def test_a_card_that_cannot_be_reused_is_not_saved(plans, organization, owner, fake_paystack):
    login_client(owner).post("/app/plan/checkout/starter/", {"interval": "month", "renews": "on"})
    _pay(PaymentAttempt.objects.get(), fake_paystack, 9002, authorization={**AUTH, "reusable": False})
    assert not PaymentMethod.objects.exists()


@override_settings(PAYMENTS_ENABLED=True)
def test_an_upgrade_also_asks_for_consent(plans, organization, owner):
    _subscribe(organization, plans["starter"])
    with pytest.raises(ValidationFailure, match="Tick the box"):
        billing.start_upgrade_checkout(organization=organization, membership=owner.memberships.get(),
                                       plan_slug="growth", return_url="https://x.test/")
    billing.start_upgrade_checkout(renewal_consent=True, organization=organization,
                                   membership=owner.memberships.get(), plan_slug="growth", return_url="https://x.test/")
    assert "Growth plan renews automatically" in RenewalConsent.objects.get().wording


# ---------- reminders ----------

def test_monthly_reminder_five_days_before_and_only_once(plans, organization, owner):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(days=6))
    assert renewals.run()["reminded"] == 0
    later = timezone.now() + timedelta(days=1, hours=1)
    assert renewals.run(later)["reminded"] == 1
    assert renewals.run(later + timedelta(hours=1))["reminded"] == 0
    email = OutboxEvent.objects.get(topic=OutboxEvent.Topic.EMAIL)
    assert owner.email in email.payload["to"]
    assert "renews on" in email.payload["body"] and "To cancel" in email.payload["body"]
    assert Notification.objects.filter(user=owner, kind="billing").exists()
    sub.refresh_from_db()
    assert sub.reminder_sent_for == sub.period_end


def test_yearly_reminder_thirty_days_before(plans, organization, owner):
    _subscribe(organization, plans["starter"], ends_in=timedelta(days=29), interval="year")
    assert renewals.run()["reminded"] == 1


# ---------- the renewal charge ----------

def test_saved_card_is_charged_the_day_before_and_the_webhook_renews(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    _save_card(organization, owner)
    old_end = sub.period_end
    counts = renewals.run()
    assert counts["charged"] == 1
    (_, _, payload), = _charges(fake_paystack)
    invoice = Invoice.objects.get(renews_period_end=old_end)
    assert payload["amount"] == invoice.amount_minor == plans["starter"].price_minor
    assert payload["authorization_code"] == "AUTH_synthetic123"
    # Charged once only, however often the job runs.
    renewals.run()
    assert len(_charges(fake_paystack)) == 1
    # Nothing is renewed until the provider confirms.
    sub.refresh_from_db()
    assert sub.period_end == old_end
    _pay(invoice.payment_attempts.get(), fake_paystack, 9101)
    sub.refresh_from_db()
    assert sub.period_start == old_end and sub.period_end > old_end + timedelta(days=27)
    assert Invoice.objects.get(pk=invoice.pk).status == InvoiceStatus.PAID


def test_a_declined_renewal_starts_grace_retries_then_pauses_and_ends(plans, organization, owner, fake_paystack, make_task):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    _save_card(organization, owner)
    fake_paystack.charge_status = "failed"
    counts = renewals.run()
    assert counts["grace"] == 1
    sub.refresh_from_db()
    # Five business days after the period ends; service continues meanwhile.
    assert sub.grace_until == renewals.add_business_days(sub.period_end, 5)
    assert Notification.objects.filter(title__icontains="didn't go through").exists()
    assert PaymentAttempt.objects.get().status == PaymentStatus.FAILED
    in_grace = sub.period_end + timedelta(days=1)
    assert billing.in_paid_time(sub, in_grace)

    # Up to three tries in all, at least two days apart.
    renewals.run(in_grace)
    renewals.run(in_grace + timedelta(hours=1))
    renewals.run(in_grace + timedelta(days=2, hours=1))
    renewals.run(in_grace + timedelta(days=4, hours=2))
    assert len(_charges(fake_paystack)) == 3

    # Last-day reminder, then pause.
    renewals.run(sub.grace_until - timedelta(hours=12))
    assert Notification.objects.filter(title="Last day to pay your renewal").count() == 1
    after = sub.grace_until + timedelta(minutes=1)
    assert renewals.run(after)["paused"] == 1
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.PAST_DUE and sub.paused_at == after
    with pytest.raises(ValidationFailure, match="paused"):
        make_task(organization, owner)

    # Thirty days paused: the plan ends.
    assert renewals.run(after + timedelta(days=30, minutes=1))["ended"] == 1
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.CANCELLED
    assert not Invoice.objects.filter(status=InvoiceStatus.ISSUED, renews_period_end__isnull=False).exists()


def test_paying_during_grace_continues_from_the_old_end(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    renewals.run()  # no card: nothing charged yet
    sub.refresh_from_db()
    Subscription.objects.filter(pk=sub.pk).update(
        period_end=timezone.now() - timedelta(days=1), grace_until=timezone.now() + timedelta(days=3)
    )
    sub.refresh_from_db()
    assert billing.active_subscription(organization) == sub
    invoice = renewals._create_renewal_invoice(sub)
    attempt = billing._create_attempt(invoice, owner, {})
    _pay(attempt, fake_paystack, 9201)
    old_end = sub.period_end
    sub.refresh_from_db()
    assert sub.period_start == old_end and sub.grace_until is None and sub.renewal_attempts == 0


def test_paying_while_paused_starts_the_period_that_day(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"])
    Subscription.objects.filter(pk=sub.pk).update(
        status=SubscriptionStatus.PAST_DUE, period_end=timezone.now() - timedelta(days=9),
        paused_at=timezone.now() - timedelta(days=2),
    )
    sub.refresh_from_db()
    invoice = renewals._create_renewal_invoice(sub)
    before = timezone.now()
    _pay(billing._create_attempt(invoice, owner, {}), fake_paystack, 9301)
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.ACTIVE and sub.period_start >= before and sub.paused_at is None


@override_settings(PAYMENTS_ENABLED=True)
def test_without_a_card_the_renewal_is_paid_by_link(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    renewals.run()
    assert not _charges(fake_paystack)
    invoice = Invoice.objects.get(renews_period_end=sub.period_end)
    renewals.run(sub.period_end + timedelta(minutes=1))
    sub.refresh_from_db()
    assert sub.grace_until is not None
    response = login_client(owner).post(f"/app/billing/invoices/{invoice.pk}/pay/")
    assert response.status_code == 302 and response["Location"].startswith("https://checkout.paystack.com/")


def test_a_bad_subscription_does_not_stop_the_others(plans, organization, owner, monkeypatch):
    _subscribe(organization, plans["starter"], ends_in=timedelta(days=4))
    monkeypatch.setattr(renewals, "_step", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert renewals.run()["reminded"] == 0  # logged, not raised


# ---------- cancelling and downgrades ----------

def test_cancel_stops_the_charge_and_keeps_access_to_the_end(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    _save_card(organization, owner)
    c = login_client(owner)
    c.post("/app/plan/cancel/")
    sub.refresh_from_db()
    assert sub.cancel_at_period_end
    renewals.run()
    assert not _charges(fake_paystack)
    assert billing.active_subscription(organization) == sub
    assert renewals.run(sub.period_end + timedelta(minutes=1))["ended"] == 1
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.CANCELLED
    assert "ended as you asked" in OutboxEvent.objects.last().payload["body"]


def test_undo_cancel_and_only_the_owner_can_cancel(plans, organization, owner, member):
    sub = _subscribe(organization, plans["starter"])
    with pytest.raises(PermissionFailure):
        renewals.cancel_renewal(organization, member.memberships.get())
    renewals.cancel_renewal(organization, owner.memberships.get())
    renewals.undo_cancel(organization, owner.memberships.get())
    sub.refresh_from_db()
    assert not sub.cancel_at_period_end


def test_cancelling_voids_a_prepared_renewal_invoice(plans, organization, owner):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    renewals.run()
    invoice = Invoice.objects.get(renews_period_end=sub.period_end)
    renewals.cancel_renewal(organization, owner.memberships.get())
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.VOID


def test_downgrade_takes_effect_at_renewal(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["growth"], ends_in=timedelta(hours=20))
    _save_card(organization, owner)
    with pytest.raises(ValidationFailure):
        renewals.schedule_downgrade(organization, owner.memberships.get(), "dedicated")
    login_client(owner).post("/app/plan/downgrade/", {"plan": "starter"})
    sub.refresh_from_db()
    assert sub.next_plan.slug == "starter" and sub.plan.slug == "growth"
    renewals.run()
    invoice = Invoice.objects.get(renews_period_end=sub.period_end)
    assert invoice.plan.slug == "starter" and invoice.amount_minor == plans["starter"].price_minor
    _pay(invoice.payment_attempts.get(), fake_paystack, 9401)
    sub.refresh_from_db()
    assert sub.plan.slug == "starter" and sub.next_plan is None


def test_removing_the_card_means_no_automatic_charge(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    _save_card(organization, owner)
    login_client(owner).post("/app/plan/card/remove/")
    assert renewals.saved_card(organization.pk) is None
    renewals.run()
    assert not _charges(fake_paystack)
    assert Invoice.objects.filter(renews_period_end=sub.period_end).exists()


def test_an_unreadable_saved_token_falls_back_to_a_pay_link(plans, organization, owner, fake_paystack):
    _subscribe(organization, plans["starter"], ends_in=timedelta(hours=20))
    method = _save_card(organization, owner)
    PaymentMethod.objects.filter(pk=method.pk).update(token_encrypted="not-a-token")
    renewals.run()
    assert not _charges(fake_paystack)


# ---------- the plan page ----------

@override_settings(PAYMENTS_ENABLED=True)
def test_plan_page_shows_renewal_and_controls(plans, organization, owner):
    _subscribe(organization, plans["growth"])
    _save_card(organization, owner)
    body = login_client(owner).get("/app/plan/").content.decode()
    assert "Renews on" in body and "Visa ending 4081" in body
    assert "Cancel renewal" in body and "Move down at renewal" in body and 'name="renews"' in body


@override_settings(PAYMENTS_ENABLED=True)
def test_plan_page_when_paused(plans, organization, owner):
    sub = _subscribe(organization, plans["starter"])
    Subscription.objects.filter(pk=sub.pk).update(
        status=SubscriptionStatus.PAST_DUE, paused_at=timezone.now(), period_end=timezone.now() - timedelta(days=8)
    )
    sub.refresh_from_db()
    renewals._create_renewal_invoice(sub)
    body = login_client(owner).get("/app/plan/").content.decode()
    assert "Your service is paused" in body and "to resume" in body


def test_flutterwave_saved_card_charge_maps_its_status(fake_flutterwave):
    from apps.billing import flutterwave

    status = flutterwave.charge_saved_card(token="flw-t-synthetic", email="o@example.test", amount_minor=5500,
                                           currency="USD", reference="BA-T1", metadata={"invoice": "INV-1"})
    assert status == "success"
    (_, _path, payload), = [c for c in fake_flutterwave.calls if c[1] == "/tokenized-charges"]
    assert payload["amount"] == "55.00" and payload["token"] == "flw-t-synthetic"
    assert flutterwave._expiry("09/32") == (9, 2032) and flutterwave._expiry(None) == (None, None)
