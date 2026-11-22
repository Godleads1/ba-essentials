"""Limits that stop attackers running up costs or testing cards (security checklist 7, 13, 14, 15, 39).

- Text messages: only to numbers in TEXT_COUNTRIES, a site-wide cap per minute and hour with an
  alert, and a pause switch. Codes then go by email.
- Checkouts: a cap per account and per connection, audited and alerted, before any payment
  provider is called.
- New sign-ups can be paused in an emergency.
"""
from __future__ import annotations

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts import codes, termii
from apps.audit.models import AuditEvent
from apps.core.exceptions import ValidationFailure
from apps.core.models import OutboxEvent

from .conftest import login_client, make_user

NIGERIA = "+2348031234567"
UK = "+447400123456"


@pytest.fixture
def phone_client(db, organization):
    from apps.organizations.models import Membership, MembershipRole

    user = make_user("texts@example.test")
    user.set_unusable_password()
    user.phone, user.phone_verified_at = NIGERIA, timezone.now()
    user.save()
    Membership.objects.create(user=user, organization=organization, role=MembershipRole.OWNER)
    return user


# ---------- 14: only numbers in TEXT_COUNTRIES get texts ----------

def test_texts_only_go_to_numbers_in_the_allowed_countries(db, fake_termii):
    assert termii.country_allowed(NIGERIA) and not termii.country_allowed(UK)
    with pytest.raises(termii.TextNotAllowed):
        termii.send_sms(UK, "x")
    with pytest.raises(termii.TextNotAllowed):
        termii.send_whatsapp_code(UK, "123456")
    assert fake_termii.sent == []


def test_signing_in_with_a_foreign_number_says_use_email(db, fake_termii):
    # The answer depends only on the number, never on whether an account has it.
    with pytest.raises(ValidationFailure, match="numbers in Nigeria. Use your email address instead."):
        codes.request_sign_in(UK, channel="sms")
    body = Client().post("/login/", {"identifier": UK, "channel": "sms"}).content.decode()
    assert "numbers in Nigeria" in body
    assert fake_termii.sent == []


@override_settings(TEXT_COUNTRIES=["NG", "GH"])
def test_more_countries_can_be_allowed(db):
    assert termii.country_allowed("+233241234567")
    assert "Nigeria, Ghana" in codes._abroad_message()


def test_a_foreign_number_at_sign_up_is_kept_but_codes_go_by_email(db, mailoutbox, fake_termii):
    pending = codes.register_client(full_name="Abroad", email="abroad@example.test", phone=UK, channel="sms")
    assert "confirm_phone" not in pending and len(mailoutbox) == 1 and fake_termii.sent == []
    from apps.accounts.models import User

    assert User.objects.get(email="abroad@example.test").code_channel == "email"


def test_adding_a_foreign_number_is_refused_before_anything_is_sent(phone_client, fake_termii):
    with pytest.raises(ValidationFailure, match="numbers in Nigeria"):
        codes.start_phone_change(phone_client, UK, channel="sms")
    assert fake_termii.sent == []


def test_a_foreign_verified_number_is_not_offered_for_codes(phone_client):
    phone_client.phone = UK
    assert codes.channels_for(phone_client) == ["email"]


# ---------- 13: a site-wide cap on texts ----------

@override_settings(TEXT_SENDS_PER_MINUTE=3, SECURITY_ALERT_EMAILS=["security@example.test"])
def test_the_site_wide_cap_stops_texts_and_alerts(db, fake_termii):
    for _ in range(3):
        termii.send_sms(NIGERIA, "x")
    assert termii.cap_reached()
    for _ in range(2):
        with pytest.raises(termii.TextNotAllowed):
            termii.send_sms(NIGERIA, "x")
    assert len(fake_termii.sent) == 3
    alerts = [e for e in OutboxEvent.objects.all() if "limit on text messages" in e.payload.get("subject", "")]
    assert len(alerts) == 1  # throttled: one alert, not one per message


@override_settings(TEXT_SENDS_PER_HOUR=2)
def test_the_hourly_cap_counts_too(db, fake_termii):
    termii.send_sms(NIGERIA, "x")
    termii.send_whatsapp_code(NIGERIA, "123456")
    with pytest.raises(termii.TextNotAllowed):
        termii.send_sms(NIGERIA, "x")


@override_settings(TEXT_SENDS_PER_MINUTE=1)
def test_when_the_cap_is_reached_signed_in_clients_get_codes_by_email(phone_client, fake_termii, mailoutbox):
    termii.send_sms(NIGERIA, "x")
    assert codes.channels_for(phone_client) == ["email"]
    codes.send_step_up_code(phone_client)
    assert len(fake_termii.sent) == 1 and len(mailoutbox) == 1
    with pytest.raises(ValidationFailure, match="aren't available right now"):
        codes.request_sign_in(NIGERIA, channel="sms")


# ---------- 39: emergency switches ----------

@override_settings(TEXT_MESSAGES_PAUSED=True)
def test_pausing_texts_sends_everything_by_email(phone_client, fake_termii, mailoutbox):
    assert codes.phone_channels() == [] and codes.channels_for(phone_client) == ["email"]
    with pytest.raises(termii.TermiiError):
        termii.send_sms(NIGERIA, "x")
    codes.send_step_up_code(phone_client)
    assert fake_termii.sent == [] and len(mailoutbox) == 1
    assert "Codes by phone aren" in Client().post("/login/", {"identifier": NIGERIA}).content.decode()


@override_settings(SIGNUPS_PAUSED=True)
def test_pausing_sign_ups(db, mailoutbox, phone_client):
    page = Client().get("/signup/")
    assert page.status_code == 200 and "New accounts are paused" in page.content.decode()
    assert '<form method="post" class="mt-9"' not in page.content.decode()
    r = Client().post("/signup/", {"full_name": "New", "email": "new@example.test", "accept_terms": "on"})
    assert r.status_code == 503 and mailoutbox == []
    with pytest.raises(ValidationFailure):
        codes.register_client(full_name="New", email="new@example.test")
    from apps.accounts.models import User

    assert not User.objects.filter(email="new@example.test").exists()
    codes.request_sign_in(phone_client.email)  # existing clients still sign in
    assert len(mailoutbox) == 1


# ---------- 15: codes last 5 minutes and allow 3 tries ----------

def test_codes_last_five_minutes(phone_client, mailoutbox, settings):
    assert settings.OTP_TTL_SECONDS == 300 and settings.OTP_MAX_ATTEMPTS == 3
    from apps.accounts.models import VerificationChallenge

    codes.request_sign_in(phone_client.email)
    challenge = VerificationChallenge.objects.get()
    assert 290 <= (challenge.expires_at - challenge.created_at).total_seconds() <= 301
    assert "expires in 5 minutes" in mailoutbox[-1].body or "5 minutes" in mailoutbox[-1].body
    page = Client()
    page.post("/login/", {"identifier": phone_client.email})
    assert "It expires in 5 minutes." in page.get("/login/code/").content.decode()


# ---------- 7: checkout attempts are limited ----------

@pytest.fixture
def plans(db):
    from django.core.management import call_command

    call_command("seed_plans", stdout=open("/dev/null", "w"))  # noqa: SIM115


@override_settings(PAYMENTS_ENABLED=True, CHECKOUT_STARTS_PER_ACCOUNT_PER_HOUR=3,
                   SECURITY_ALERT_EMAILS=["security@example.test"])
def test_one_account_can_only_start_so_many_checkouts(plans, owner, fake_paystack):
    c = login_client(owner)
    for _ in range(3):
        r = c.post("/app/plan/checkout/starter/")
        assert r.status_code == 302 and r["Location"].startswith("https://checkout.paystack.com/")
    calls = len(fake_paystack.calls)
    r = c.post("/app/plan/checkout/starter/", follow=True)
    assert "paused new ones for this hour" in r.content.decode()
    assert len(fake_paystack.calls) == calls  # Paystack is never asked
    event = AuditEvent.objects.get(action="billing.checkout_limited")
    assert event.actor_id == owner.pk and event.result == AuditEvent.Result.DENIED
    assert [e.payload["to"] for e in OutboxEvent.objects.all()
            if "Too many checkouts" in e.payload.get("subject", "")] == [["security@example.test"]]


@override_settings(PAYMENTS_ENABLED=True, CHECKOUT_STARTS_PER_IP_PER_HOUR=2)
def test_one_connection_can_only_start_so_many_checkouts(plans, owner, other_owner, fake_paystack):
    login_client(owner).post("/app/plan/checkout/starter/")
    login_client(other_owner).post("/app/plan/checkout/starter/")
    calls = len(fake_paystack.calls)
    r = login_client(owner).post("/app/plan/checkout/starter/")  # a third account would be the same
    assert r.status_code == 302 and r["Location"] == "/app/plan/"
    assert len(fake_paystack.calls) == calls


@override_settings(PAYMENTS_ENABLED=True, CHECKOUT_STARTS_PER_ACCOUNT_PER_HOUR=1)
def test_upgrades_and_invoices_share_the_limit(plans, owner, organization, fake_paystack):
    from apps.billing.models import Invoice

    c = login_client(owner)
    c.post("/app/plan/checkout/starter/")
    calls = len(fake_paystack.calls)
    assert c.post("/app/plan/upgrade/growth/")["Location"] == "/app/plan/"
    invoice = Invoice.objects.filter(organization=organization).first()
    if invoice is not None:
        c.post(f"/app/billing/invoices/{invoice.pk}/pay/")
    assert len(fake_paystack.calls) == calls
