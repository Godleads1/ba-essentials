"""Step 4b: changing a phone number or email safely, the optional authenticator for
clients, and signing out other devices for real (guide 4.1)."""
from __future__ import annotations

import re
from datetime import timedelta

import pyotp
import pytest
from django.contrib.sessions.models import Session
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts import codes, services
from apps.accounts.models import StaffRole, User, UserSession, VerificationChallenge
from apps.core.exceptions import ValidationFailure

from .conftest import login_client, make_user

PHONE = "+2348031234567"


def _mail_code(mailoutbox, to=None):
    msgs = [m for m in mailoutbox if to is None or to in m.to]
    return re.search(r"\b(\d{6})\b", msgs[-1].body).group(1)


@pytest.fixture
def client_user(db, organization):
    from apps.organizations.models import Membership, MembershipRole

    user = make_user("client@example.test")
    user.set_unusable_password()
    user.phone, user.phone_verified_at = PHONE, timezone.now()
    user.save()
    Membership.objects.create(user=user, organization=organization, role=MembershipRole.OWNER)
    return user


def _fresh(c, user):
    session = c.session
    services.mark_step_up(type("R", (), {"session": session})(), user)
    session.save()
    return c


# ---------- email ----------

def test_change_email_needs_a_fresh_check_then_a_code_to_the_new_address(client_user, mailoutbox):
    c = login_client(client_user)
    r = c.get("/app/security/email/")
    assert r.status_code == 302 and r["Location"].startswith("/step-up/")
    _fresh(c, client_user)
    c.post("/app/security/email/", {"email": "New@Example.TEST"})
    assert mailoutbox[-1].to == ["new@example.test"]
    r = c.post("/app/security/email/", {"code": _mail_code(mailoutbox, "new@example.test")})
    assert r["Location"] == "/app/security/"
    client_user.refresh_from_db()
    assert client_user.email == "new@example.test" and client_user.email_verified
    notice = [m for m in mailoutbox if m.to == ["client@example.test"]][-1]
    assert "was changed" in notice.subject and "support@" in notice.body
    # This device stays signed in.
    assert c.get("/app/security/").status_code == 200


def test_an_email_another_account_uses_gets_the_same_answer_and_no_code(client_user, mailoutbox):
    make_user("taken@example.test")
    mailoutbox.clear()
    assert codes.start_email_change(client_user, "taken@example.test") == codes.phones.masked_email("taken@example.test")
    assert mailoutbox == []
    with pytest.raises(ValidationFailure):
        codes.confirm_email_change(client_user, "taken@example.test", "123456")


def test_changing_email_signs_out_other_devices(client_user, mailoutbox):
    other = login_client(client_user)
    codes.start_email_change(client_user, "new@example.test")
    codes.confirm_email_change(client_user, "new@example.test", _mail_code(mailoutbox, "new@example.test"))
    assert other.get("/app/security/").status_code == 302


# ---------- phone ----------

def test_change_phone_sends_the_code_to_the_new_number_and_tells_the_old(client_user, fake_termii, mailoutbox):
    c = _fresh(login_client(client_user), client_user)
    c.post("/app/security/phone/", {"phone": "0805 000 1111", "channel": "sms"})
    assert fake_termii.sent[-1]["to"] == "2348050001111"
    c.post("/app/security/phone/", {"code": fake_termii.last_code()})
    client_user.refresh_from_db()
    assert client_user.phone == "+2348050001111" and client_user.phone_verified
    assert fake_termii.sent[-1]["to"] == "2348031234567" and "changed" in fake_termii.sent[-1]["sms"]
    assert "phone number was changed" in mailoutbox[-1].subject


def test_a_number_another_account_has_confirmed_cannot_be_taken(client_user, fake_termii):
    other = make_user("other@example.test")
    User.objects.filter(pk=other.pk).update(phone="+2348050002222", phone_verified_at=timezone.now())
    fake_termii.sent.clear()
    codes.start_phone_change(client_user, "+2348050002222", channel="sms")
    assert fake_termii.sent == []  # same answer, no code
    with pytest.raises(ValidationFailure):
        codes.confirm_phone_change(client_user, "+2348050002222", "123456")


def test_remove_phone(client_user, fake_termii, mailoutbox):
    c = _fresh(login_client(client_user), client_user)
    c.post("/app/security/phone/remove/")
    client_user.refresh_from_db()
    assert client_user.phone == "" and client_user.code_channel == "email"
    assert "removed" in mailoutbox[-1].subject


def test_buttons_without_a_fresh_check_go_to_the_check_then_back(client_user):
    r = login_client(client_user).post("/app/security/phone/remove/")
    assert r["Location"] == "/step-up/?next=%2Fapp%2Fsecurity%2F"
    client_user.refresh_from_db()
    assert client_user.phone == PHONE


# ---------- authenticator for clients ----------

def test_clients_can_turn_the_authenticator_on_and_off(client_user, mailoutbox):
    c = _fresh(login_client(client_user), client_user)
    assert "Set up an authenticator app" in c.get("/app/security/mfa/").content.decode()
    client_user.refresh_from_db()
    secret = services.mfa.decrypt_secret(client_user.mfa_secret_encrypted)
    c.post("/app/security/mfa/", {"code": pyotp.TOTP(secret).now()})
    client_user.refresh_from_db()
    assert client_user.mfa_enabled and "turned on" in mailoutbox[-1].subject
    # Signing in now asks for the app's code after the sign-in code.
    s = Client()
    s.post("/login/", {"identifier": client_user.email})
    r = s.post("/login/code/", {"code": _mail_code(mailoutbox)})
    assert r["Location"] == "/login/mfa/"
    # Turning it off needs a code from the app.
    User.objects.filter(pk=client_user.pk).update(mfa_last_timestep=None)
    c.post("/app/security/mfa/off/", {"code": "000000"})
    client_user.refresh_from_db()
    assert client_user.mfa_enabled
    c.post("/app/security/mfa/off/", {"code": pyotp.TOTP(secret).now()})
    client_user.refresh_from_db()
    assert not client_user.mfa_enabled and "turned off" in mailoutbox[-1].subject


def test_staff_cannot_turn_their_authenticator_off(db):
    staff = make_user("ops2@example.test", staff_role=StaffRole.OPERATIONS, mfa=True, is_staff=True)
    with pytest.raises(ValidationFailure):
        codes.disable_mfa(staff)


# ---------- devices ----------

def test_signing_out_a_device_really_ends_its_session(client_user, mailoutbox):
    devices = []
    for _ in range(2):
        d = Client()
        d.post("/login/", {"identifier": client_user.email})
        d.post("/login/code/", {"code": _mail_code(mailoutbox)})
        VerificationChallenge.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        devices.append(d)
    first, second = devices
    key = second.session.session_key
    first.post(f"/app/security/sessions/{key}/revoke/")
    assert not Session.objects.filter(session_key=key).exists()
    assert second.get("/app/security/").status_code == 302
    assert first.get("/app/security/").status_code == 200


def test_sign_out_all_other_devices(client_user, mailoutbox):
    devices = []
    for _ in range(3):
        d = Client()
        d.post("/login/", {"identifier": client_user.email})
        d.post("/login/code/", {"code": _mail_code(mailoutbox)})
        VerificationChallenge.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        devices.append(d)
    devices[0].post("/app/security/sessions/others/revoke/")
    assert devices[0].get("/app/security/").status_code == 200
    assert all(d.get("/app/security/").status_code == 302 for d in devices[1:])
    assert UserSession.objects.filter(user=client_user, revoked_at__isnull=True).count() == 1


def test_you_cannot_sign_out_someone_elses_session(client_user, mailoutbox):
    other = make_user("other2@example.test")
    o = login_client(other)
    key = o.session.session_key
    UserSession.objects.create(user=other, session_key=key)
    login_client(client_user).post(f"/app/security/sessions/{key}/revoke/")
    assert Session.objects.filter(session_key=key).exists()


def test_security_page_uses_the_banda_design(client_user):
    body = login_client(client_user).get("/app/security/").content.decode()
    assert "banda/app.css" in body and "Your contact details" in body and "Where you&rsquo;re signed in" in body


def test_a_code_only_works_for_the_address_it_was_sent_to(client_user, mailoutbox):
    make_user("taken3@example.test")
    codes.start_email_change(client_user, "mine@example.test")
    code_for_mine = _mail_code(mailoutbox, "mine@example.test")
    VerificationChallenge.objects.update(created_at=timezone.now() - timedelta(minutes=2))
    codes.start_email_change(client_user, "taken3@example.test")
    with pytest.raises(ValidationFailure):
        codes.confirm_email_change(client_user, "mine@example.test", code_for_mine)


def test_a_code_for_one_address_cannot_confirm_another(client_user, mailoutbox):
    # Two tabs: one asks for X, the other for Y. Y's code must not confirm X.
    codes.start_email_change(client_user, "x@example.test")
    VerificationChallenge.objects.update(created_at=timezone.now() - timedelta(minutes=2))
    codes.start_email_change(client_user, "y@example.test")
    code_for_y = _mail_code(mailoutbox, "y@example.test")
    with pytest.raises(ValidationFailure):
        codes.confirm_email_change(client_user, "x@example.test", code_for_y)
    assert codes.confirm_email_change(client_user, "y@example.test", code_for_y).email == "y@example.test"


@override_settings(OTP_RESEND_COOLDOWN_SECONDS=60)
def test_the_resend_wait_is_the_same_for_taken_and_free_addresses(client_user, mailoutbox):
    from apps.core.exceptions import RateLimited

    make_user("taken4@example.test")
    codes.start_email_change(client_user, "first@example.test")
    for address in ("taken4@example.test", "free@example.test"):
        with pytest.raises(RateLimited):
            codes.start_email_change(client_user, address)


def test_a_failed_sign_out_can_be_retried(client_user, mailoutbox, monkeypatch):
    d = Client()
    d.post("/login/", {"identifier": client_user.email})
    d.post("/login/code/", {"code": _mail_code(mailoutbox)})
    key = d.session.session_key
    monkeypatch.setattr(services, "_end_django_sessions", lambda keys: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(RuntimeError):
        services.revoke_other_sessions(client_user, "another-key")
    assert UserSession.objects.get(session_key=key).revoked_at is None  # still listed, so it can be retried
    monkeypatch.undo()
    assert services.revoke_other_sessions(client_user, "another-key") == 1
    assert not Session.objects.filter(session_key=key).exists()


def test_a_bad_phone_entry_shows_the_error_not_unavailable(client_user):
    c = _fresh(login_client(client_user), client_user)
    body = c.post("/app/security/phone/", {"phone": "12", "channel": "sms"}).content.decode()
    assert "Enter a valid phone number" in body and "aren&rsquo;t available" not in body


def test_the_setup_key_stays_the_same_until_set_up_is_finished(db):
    """Background requests used to replace the key the person was typing into their app."""
    import re as _re

    staff = make_user("newadmin@example.test", staff_role=StaffRole.OPERATIONS, is_staff=True)
    c = login_client(staff)
    _fresh(c, staff)
    key = lambda body: _re.search(r"tracking-wider text-ink\">([A-Z2-7]{16,})<", body).group(1)  # noqa: E731
    first = key(c.get("/app/security/mfa/").content.decode())
    c.get("/favicon.ico")  # redirected to set-up for staff without an authenticator
    c.get("/meta.json")
    assert key(c.get("/app/security/mfa/").content.decode()) == first
    staff.refresh_from_db()
    c.post("/app/security/mfa/", {"code": pyotp.TOTP(first).now()})
    staff.refresh_from_db()
    assert staff.mfa_enabled
    # Asking for a new key on purpose still works.
    other = make_user("newadmin2@example.test", staff_role=StaffRole.OPERATIONS, is_staff=True)
    c2 = _fresh(login_client(other), other)
    k1 = key(c2.get("/app/security/mfa/").content.decode())
    assert key(c2.get("/app/security/mfa/?new=1").content.decode()) != k1
