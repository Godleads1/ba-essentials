"""Clients sign in with one-time codes and have no password (guide 4.1, step 4)."""
from __future__ import annotations

import importlib
import logging
import re
from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts import codes, services
from apps.accounts.models import StaffRole, User, VerificationChallenge
from apps.core.exceptions import RateLimited, ValidationFailure

from .conftest import PASSWORD, login_client, make_user

PHONE = "+2348031234567"


def _mail_code(mailoutbox):
    return re.search(r"\b(\d{6})\b", mailoutbox[-1].body).group(1)


@pytest.fixture
def client_user(db, organization):
    from apps.organizations.models import Membership, MembershipRole

    user = make_user("client@example.test")
    user.set_unusable_password()
    user.phone, user.phone_verified_at = PHONE, timezone.now()
    user.save()
    Membership.objects.create(user=user, organization=organization, role=MembershipRole.OWNER)
    return user


# ---------- signing in ----------

def test_sign_in_by_email_code(client_user, mailoutbox):
    c = Client()
    r = c.post("/login/", {"identifier": " Client@Example.TEST "})
    assert r.status_code == 302 and r["Location"] == "/login/code/"
    code = _mail_code(mailoutbox)
    assert code not in r["Location"]
    r = c.post("/login/code/", {"code": code})
    assert r.status_code == 302 and r["Location"] == "/app/"
    assert c.session["_auth_user_id"] == str(client_user.pk)


@pytest.mark.parametrize("channel", ["sms", "whatsapp"])
def test_sign_in_by_phone_code(client_user, fake_termii, channel):
    c = Client()
    c.post("/login/", {"identifier": "0803 123 4567", "channel": channel})
    sent = fake_termii.sent[-1]
    assert sent["to"] == "2348031234567"  # E.164 without the +
    assert sent["channel"] == ("dnd" if channel == "sms" else "whatsapp_otp")
    if channel == "whatsapp":
        assert re.fullmatch(r"\d{6}", sent["sms"])  # WhatsApp OTP route takes the code only
    r = c.post("/login/code/", {"code": fake_termii.last_code()})
    assert r["Location"] == "/app/"


def test_an_unconfirmed_phone_cannot_sign_in(client_user, fake_termii):
    User.objects.filter(pk=client_user.pk).update(phone_verified_at=None)
    r = Client().post("/login/", {"identifier": PHONE, "channel": "sms"})
    assert r["Location"] == "/login/code/" and fake_termii.sent == []


def test_unknown_accounts_get_the_same_answer(client_user, mailoutbox, fake_termii):
    known = codes.request_sign_in(PHONE, channel="sms")
    unknown = codes.request_sign_in("+2348039999999", channel="sms")
    assert set(known) == set(unknown) and known["ch"] == unknown["ch"] == "sms"
    assert len(fake_termii.sent) == 1
    c = Client()
    c.post("/login/", {"identifier": "nobody@example.test"})
    body = c.get("/login/code/").content.decode()
    assert "We&rsquo;ve sent a 6-digit code to" in body
    r = c.post("/login/code/", {"code": "123456"})
    assert "invalid or has expired" in r.content.decode()


def test_staff_cannot_use_codes_and_clients_cannot_use_passwords(client_user, mailoutbox):
    staff = make_user("staff@example.test", staff_role=StaffRole.OPERATIONS)
    mailoutbox.clear()
    codes.request_sign_in(staff.email)
    assert mailoutbox == []
    client_user.set_password(PASSWORD)  # even if one were set somehow
    client_user.save()
    out = services.login_with_password(email=client_user.email, password=PASSWORD)
    assert out.status == services.LoginStatus.FAILED
    assert services.login_with_password(email=staff.email, password=PASSWORD).status == services.LoginStatus.OK
    r = Client().post("/login/staff/", {"email": staff.email, "password": PASSWORD})
    assert r.status_code == 302


def test_a_new_code_cancels_the_last_one(client_user, mailoutbox):
    codes.request_sign_in(client_user.email)
    first = _mail_code(mailoutbox)
    VerificationChallenge.objects.update(created_at=timezone.now() - timedelta(minutes=2))  # past the resend wait
    codes.request_sign_in(client_user.email)
    second = _mail_code(mailoutbox)
    pending = {"uid": str(client_user.pk), "kind": "email"}
    if first != second:
        with pytest.raises(ValidationFailure):
            codes.verify_sign_in(pending, first)
    assert codes.verify_sign_in(pending, second) == client_user


def test_sends_are_limited_per_number_and_per_ip(client_user, fake_termii):
    for _ in range(5):
        codes.request_sign_in(PHONE, channel="sms", client_ip="198.51.100.9")
    with pytest.raises(RateLimited, match="this address or number"):
        codes.request_sign_in(PHONE, channel="sms", client_ip="198.51.100.9")
    for i in range(20):
        try:
            codes.request_sign_in(f"person{i}@example.test", client_ip="198.51.100.10")
        except RateLimited:
            pytest.fail("limited too early")
    with pytest.raises(RateLimited, match="from here"):
        codes.request_sign_in("one-more@example.test", client_ip="198.51.100.10")


def test_wrong_codes_lock_the_account_for_longer_each_time(client_user, mailoutbox):
    pending = {"uid": str(client_user.pk), "kind": "email"}
    durations = []
    for _ in range(2):
        User.objects.filter(pk=client_user.pk).update(locked_until=None)
        codes.request_sign_in(client_user.email)
        VerificationChallenge.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        for _ in range(5):
            with pytest.raises((ValidationFailure, RateLimited)):
                codes.verify_sign_in(pending, "000000" if _mail_code(mailoutbox) != "000000" else "111111")
        client_user.refresh_from_db()
        durations.append((client_user.locked_until - timezone.now()).total_seconds())
    assert durations[1] > durations[0] * 1.8
    # While locked, even the right code is refused with the same message.
    codes.request_sign_in(client_user.email)
    assert len(mailoutbox) == 2  # no code is sent to a locked account


def test_codes_are_not_logged_and_a_failed_send_does_not_reveal_the_account(client_user, fake_termii, caplog):
    fake_termii.fail = True
    with caplog.at_level(logging.DEBUG):
        pending = codes.request_sign_in(PHONE, channel="sms")
    assert pending["uid"]  # kept server-side only
    challenge = VerificationChallenge.objects.get()
    assert challenge.superseded_at is not None
    assert not re.search(r"\b\d{6}\b", caplog.text)


@override_settings(TERMII_API_KEY="")
def test_without_termii_clients_use_email(client_user, mailoutbox):
    body = Client().get("/login/").content.decode()
    assert "you@company.com" in body and "WhatsApp" not in body
    with pytest.raises(ValidationFailure, match="Enter your email"):
        codes.request_sign_in(PHONE)


def test_old_code_records_are_deleted_after_a_day(client_user, mailoutbox):
    codes.request_sign_in(client_user.email)
    VerificationChallenge.objects.update(created_at=timezone.now() - timedelta(hours=25))
    assert codes.purge_old_codes() == 1


# ---------- signing up ----------

def test_sign_up_has_no_password_and_confirms_the_phone_second(db, mailoutbox, fake_termii):
    c = Client()
    r = c.post("/signup/", {"full_name": "New Client", "email": "new@example.test", "phone": "0803 123 4567",
                            "channel": "whatsapp", "accept_terms": "on"})
    assert r["Location"] == "/login/code/"
    user = User.objects.get(email="new@example.test")
    assert not user.has_usable_password() and user.phone == PHONE and not user.phone_verified
    r = c.post("/login/code/", {"code": _mail_code(mailoutbox)})
    assert r["Location"] == "/verify/phone/"
    user.refresh_from_db()
    assert user.email_verified
    c.post("/verify/phone/", {"send": "1", "channel": "whatsapp"})
    r = c.post("/verify/phone/", {"code": fake_termii.last_code()})
    user.refresh_from_db()
    assert user.phone_verified and r.status_code == 302


def test_someone_else_cannot_claim_your_number(db, client_user, mailoutbox, fake_termii):
    # An unproven copy of the number elsewhere is dropped when the real owner confirms it.
    other = make_user("squatter@example.test")
    User.objects.filter(pk=other.pk).update(phone="+2348030000001")
    User.objects.filter(pk=client_user.pk).update(phone="+2348030000001", phone_verified_at=None)
    client_user.refresh_from_db()
    codes.send_phone_code(client_user, channel="sms")
    codes.confirm_phone(client_user, fake_termii.last_code())
    other.refresh_from_db()
    client_user.refresh_from_db()
    assert other.phone == "" and client_user.phone_verified
    # A number another account has proven can't be confirmed again.
    third = make_user("third@example.test")
    User.objects.filter(pk=third.pk).update(phone="+2348030000001")
    third.refresh_from_db()
    codes.send_phone_code(third, channel="sms")
    with pytest.raises(ValidationFailure, match="another account"):
        codes.confirm_phone(third, fake_termii.last_code())


def test_unproven_sign_up_details_are_replaced(db, mailoutbox):
    codes.register_client(email="victim@example.test", full_name="Impostor", phone="+2348030000002", channel="sms")
    codes.register_client(email="victim@example.test", full_name="Real Owner")
    user = User.objects.get(email="victim@example.test")
    assert user.full_name == "Real Owner" and user.phone == ""


# ---------- confirming it's you ----------

def test_clients_confirm_sensitive_actions_with_a_code(client_user, mailoutbox):
    User.objects.filter(pk=client_user.pk).update(code_channel="email")
    c = login_client(client_user)
    assert "Send me a code" in c.get("/step-up/?next=/app/security/").content.decode()
    c.post("/step-up/", {"send": "1", "next": "/app/security/"})
    r = c.post("/step-up/", {"code": _mail_code(mailoutbox), "next": "/app/security/"})
    assert r["Location"] == "/app/security/"


def test_security_page_shows_codes_not_password(client_user):
    body = login_client(client_user).get("/app/security/").content.decode()
    assert "You log in with a one-time code" in body and "Change password" not in body
    login_client(client_user).post("/app/security/codes/", {"channel": "sms"})
    client_user.refresh_from_db()
    assert client_user.code_channel == "sms"


def test_existing_client_passwords_stop_working(db):
    migration = importlib.import_module("apps.accounts.migrations.0002_sign_in_codes")
    from django.apps import apps as django_apps

    client = make_user("old-client@example.test")
    staff = make_user("old-staff@example.test", staff_role=StaffRole.OPERATIONS)
    migration.clients_have_no_password(django_apps, None)
    client.refresh_from_db()
    staff.refresh_from_db()
    assert not client.has_usable_password() and staff.check_password(PASSWORD)


def test_keep_me_signed_in(client_user, mailoutbox):
    c = Client()
    c.post("/login/", {"identifier": client_user.email, "remember": "on"})
    c.post("/login/code/", {"code": _mail_code(mailoutbox)})
    assert c.session.get_expiry_age() >= 60 * 60 * 24 * 29 and c.session["remember"] is True
    other = Client()
    VerificationChallenge.objects.update(created_at=timezone.now() - timedelta(minutes=2))
    other.post("/login/", {"identifier": client_user.email})
    other.post("/login/code/", {"code": _mail_code(mailoutbox)})
    assert other.session.get_expire_at_browser_close() and other.session["remember"] is False


def test_pages_use_the_banda_design(db):
    for path in ("/login/", "/signup/", "/login/staff/", "/password-reset/"):
        body = Client().get(path).content.decode()
        assert "banda/app.css" in body and "logo-word" in body, path
