from __future__ import annotations

import re
import time
from datetime import timedelta

import pyotp
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import (
    ChallengePurpose,
    User,
    UserSession,
    VerificationChallenge,
)
from apps.audit.models import AuditEvent
from apps.core.exceptions import RateLimited, ValidationFailure

from .conftest import PASSWORD, make_user


def _code_from_mail(mailoutbox) -> str:
    match = re.search(r"\b(\d{6})\b", mailoutbox[-1].body)
    assert match is not None, mailoutbox[-1].body
    return match.group(1)


def _register(email: str = "new@example.test") -> User:
    user = services.register_user(
        email=email, full_name="New Synthetic", password=PASSWORD
    )
    assert user is not None
    return user


def test_registration_normalises_email_and_sends_one_code(db, mailoutbox):
    user = _register("  New@Example.TEST ")

    assert user.email == "new@example.test"
    assert user.email_verified_at is None
    assert len(mailoutbox) == 1
    code = _code_from_mail(mailoutbox)
    services.verify_challenge(user, ChallengePurpose.EMAIL_VERIFICATION, code)

    user.refresh_from_db()
    assert user.email_verified_at is not None


def test_verification_code_is_single_use(db, mailoutbox):
    user = _register()
    code = _code_from_mail(mailoutbox)
    services.verify_challenge(user, ChallengePurpose.EMAIL_VERIFICATION, code)

    with pytest.raises(ValidationFailure):
        services.verify_challenge(user, ChallengePurpose.EMAIL_VERIFICATION, code)


def test_verification_attempts_are_capped(db, mailoutbox):
    user = _register()
    real_code = _code_from_mail(mailoutbox)
    wrong = "000000" if real_code != "000000" else "111111"

    for _ in range(5):
        with pytest.raises(ValidationFailure):
            services.verify_challenge(user, ChallengePurpose.EMAIL_VERIFICATION, wrong)

    with pytest.raises(RateLimited):
        services.verify_challenge(user, ChallengePurpose.EMAIL_VERIFICATION, real_code)


def test_expired_code_is_rejected(db, mailoutbox):
    user = _register()
    code = _code_from_mail(mailoutbox)
    VerificationChallenge.objects.filter(user=user).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    with pytest.raises(ValidationFailure):
        services.verify_challenge(user, ChallengePurpose.EMAIL_VERIFICATION, code)


def test_credential_change_invalidates_outstanding_code(db, mailoutbox):
    user = _register()
    code = _code_from_mail(mailoutbox)
    user.bump_credential_version()

    with pytest.raises(ValidationFailure):
        services.verify_challenge(user, ChallengePurpose.EMAIL_VERIFICATION, code)

    challenge = VerificationChallenge.objects.filter(user=user).first()
    assert challenge.superseded_at is not None


def test_duplicate_signup_does_not_reveal_account_state(db, mailoutbox, owner):
    mailoutbox.clear()
    result = services.register_user(
        email=owner.email, full_name="Someone Else", password=PASSWORD
    )

    assert result is None
    assert len(mailoutbox) == 1
    assert "already exists" in mailoutbox[0].body.lower()
    assert User.objects.filter(email=owner.email).count() == 1


def test_password_login_requires_verification(db, owner):
    unverified = make_user("unverified@example.test")
    User.objects.filter(pk=unverified.pk).update(email_verified_at=None)
    unverified.refresh_from_db()

    outcome = services.login_with_password(
        email=unverified.email, password=PASSWORD, client_ip="198.51.100.1"
    )

    assert outcome.status == services.LoginStatus.UNVERIFIED


def test_repeated_wrong_passwords_lock_account(db, owner):
    for _ in range(5):
        outcome = services.login_with_password(
            email=owner.email, password="Wrong-Pass-2026!", client_ip="198.51.100.2"
        )
        assert outcome.status == services.LoginStatus.FAILED

    owner.refresh_from_db()
    assert owner.locked_until is not None

    blocked = services.login_with_password(
        email=owner.email, password=PASSWORD, client_ip="198.51.100.2"
    )
    assert blocked.status == services.LoginStatus.FAILED
    assert AuditEvent.objects.filter(action="auth.lockout").exists()


def test_login_is_rate_limited_by_address_and_ip(db):
    for _ in range(10):
        services.login_with_password(
            email="nobody@example.test",
            password="Wrong-Pass-2026!",
            client_ip="198.51.100.3",
        )

    limited = services.login_with_password(
        email="nobody@example.test",
        password="Wrong-Pass-2026!",
        client_ip="198.51.100.3",
    )
    assert limited.status == services.LoginStatus.RATE_LIMITED


def test_mfa_enrollment_and_replay_protection(db):
    user = make_user("mfa@example.test")
    secret = services.begin_mfa_enrollment(user)
    services.confirm_mfa_enrollment(user, pyotp.TOTP(secret).now())

    user.refresh_from_db()
    assert user.mfa_enabled is True

    # Simulate a later login: the previous timestep must not replay.
    User.objects.filter(pk=user.pk).update(mfa_last_timestep=None)
    code = pyotp.TOTP(secret).now()
    assert services.verify_mfa(user, code) is True
    assert services.verify_mfa(user, code) is False


def test_change_password_bumps_version_and_revokes_other_sessions(db, owner):
    UserSession.objects.create(user=owner, session_key="other-browser")
    new_password = "Another-Pass-2026!"

    services.change_password(
        owner, current_password=PASSWORD, new_password=new_password
    )

    owner.refresh_from_db()
    assert owner.credential_version == 2
    assert owner.check_password(new_password)
    assert UserSession.objects.get(session_key="other-browser").revoked_at is not None


def test_password_reuse_is_rejected(db, owner):
    with pytest.raises(ValidationFailure):
        services.change_password(
            owner, current_password=PASSWORD, new_password=PASSWORD
        )


def test_reset_password_revokes_all_sessions(db, owner):
    UserSession.objects.create(user=owner, session_key="web-1")
    services.reset_password(owner, "Fresh-Synthetic-Pass-2026!")

    owner.refresh_from_db()
    assert UserSession.objects.get(session_key="web-1").revoked_at is not None
    assert owner.check_password("Fresh-Synthetic-Pass-2026!")


def test_step_up_stamp_expires_and_is_bound_to_credential_version(db, owner):
    factory = RequestFactory()
    request = factory.get("/step-up/")
    request.session = {}

    services.mark_step_up(request, owner)
    assert services.has_fresh_step_up(request, owner)

    request.session["step_up_at"] = int(time.time()) - 100_000
    assert not services.has_fresh_step_up(request, owner)

    services.mark_step_up(request, owner)
    owner.bump_credential_version()
    assert not services.has_fresh_step_up(request, owner)


def test_session_with_outdated_credential_version_is_rejected(db, client, owner):
    client.force_login(owner)
    session = client.session
    session["cv"] = owner.credential_version
    session["last_seen"] = int(time.time())
    session.save()

    response = client.get("/app/")
    assert response.status_code == 200

    owner.bump_credential_version()
    response = client.get("/app/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login")


def test_session_without_credential_version_never_renders_private_pages(db, owner):
    from django.test import Client

    bare = Client()
    bare.force_login(owner)
    response = bare.get("/app/")
    assert response.status_code == 302
    assert response["Location"].startswith("/login")
