from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from functools import lru_cache

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit_service
from apps.audit.models import AuditEvent
from apps.core import ratelimit
from apps.core.exceptions import RateLimited, ValidationFailure
from apps.core.ratelimit import fingerprint

from . import mfa
from .models import (
    ChallengePurpose,
    PasswordHistory,
    User,
    UserSession,
    VerificationChallenge,
)

logger = logging.getLogger("ba.accounts")

PASSWORD_HISTORY_DEPTH = 5


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return make_password("a-value-that-is-never-a-real-password")


def _hash_code(code: str, salt: str, credential_version: int) -> str:
    message = f"{salt}:{credential_version}:{code}".encode("utf-8")
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def _challenge_message(purpose: str) -> tuple[str, str]:
    if purpose == ChallengePurpose.SIGN_IN:
        return (
            "Your B & A Essentials sign-in code",
            "Your code to sign in is: {code}\n\n"
            "It expires in {minutes} minutes and can only be used once. Never share it.\n"
            "If you did not try to sign in, you can ignore this email.",
        )
    if purpose == ChallengePurpose.EMAIL_CHANGE:
        return (
            "Confirm your new email address",
            "Your code to confirm this as your B & A Essentials email is: {code}\n\n"
            "It expires in {minutes} minutes. Never share it.\n"
            "If you did not ask for this, you can ignore this email.",
        )
    if purpose == ChallengePurpose.STEP_UP:
        return (
            "Confirm it's you",
            "Your code to confirm it's you is: {code}\n\n"
            "It expires in {minutes} minutes. Never share it.\n"
            "If you did not ask for this, sign in and check your account's security page.",
        )
    if purpose == ChallengePurpose.PASSWORD_RESET:
        return (
            "Reset your B & A Essentials password",
            "Use this code to reset your password: {code}\n\n"
            "It expires in {minutes} minutes and can only be used once.\n"
            "If you did not ask for this, you can ignore this email.",
        )
    return (
        "Verify your B & A Essentials email address",
        "Use this code to finish creating your account: {code}\n\n"
        "It expires in {minutes} minutes and can only be used once.\n"
        "If you did not ask for this, you can ignore this email.",
    )


def _send_code_email(user: User, purpose: str, code: str, to: str = "") -> None:
    subject, body = _challenge_message(purpose)
    minutes = max(1, settings.OTP_TTL_SECONDS // 60)
    try:
        send_mail(
            subject,
            body.format(code=code, minutes=minutes),
            settings.DEFAULT_FROM_EMAIL,
            [to or user.email],
        )
    except Exception:
        logger.exception("challenge_email_failed purpose=%s user=%s", purpose, user.pk)
        raise ValidationFailure(
            "We could not send the verification email. Please try again shortly."
        )


def _target_hash(target: str) -> str:
    if not target:
        return ""
    return hmac.new(settings.SECRET_KEY.encode(), f"target:{target.strip().lower()}".encode(), hashlib.sha256).hexdigest()


def issue_challenge(
    user: User, purpose: str, *, enforce_cooldown: bool = True, channel: str = "", target: str = ""
) -> VerificationChallenge:
    now = timezone.now()
    with transaction.atomic():
        previous = (
            VerificationChallenge.objects.select_for_update()
            .filter(user=user, purpose=purpose)
            .order_by("-created_at")
            .first()
        )
        if (
            enforce_cooldown
            and previous
            and previous.consumed_at is None
            and (now - previous.created_at).total_seconds()
            < settings.OTP_RESEND_COOLDOWN_SECONDS
        ):
            raise RateLimited("Please wait before requesting another code.")

        VerificationChallenge.objects.filter(
            user=user, purpose=purpose, consumed_at__isnull=True, superseded_at__isnull=True
        ).update(superseded_at=now)

        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(8)
        challenge = VerificationChallenge.objects.create(
            user=user,
            purpose=purpose,
            channel=channel,
            target_hash=_target_hash(target),
            code_hash=_hash_code(code, salt, user.credential_version),
            salt=salt,
            credential_version=user.credential_version,
            expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
            max_attempts=settings.OTP_MAX_ATTEMPTS,
        )
    challenge.plaintext_code = code  # type: ignore[attr-defined]
    return challenge


def send_challenge(user: User, purpose: str) -> None:
    challenge = issue_challenge(user, purpose)
    try:
        _send_code_email(user, purpose, challenge.plaintext_code)  # type: ignore[attr-defined]
    finally:
        del challenge.plaintext_code


def verify_challenge(user: User, purpose: str, code: str, *, target: str = "") -> User:
    """Check a code. With `target`, it must also be the code sent to that address or number."""
    now = timezone.now()
    failure: str | None = None
    too_many = False

    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        challenge = (
            VerificationChallenge.objects.select_for_update()
            .filter(
                user=locked_user,
                purpose=purpose,
                consumed_at__isnull=True,
                superseded_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if challenge is None or challenge.expires_at <= now:
            raise ValidationFailure("That code is invalid or has expired.")
        if target and not hmac.compare_digest(challenge.target_hash or "", _target_hash(target)):
            raise ValidationFailure("That code is invalid or has expired.")
        if challenge.attempts >= challenge.max_attempts:
            too_many = True
        elif challenge.credential_version != locked_user.credential_version:
            challenge.superseded_at = now
            challenge.save(update_fields=["superseded_at"])
            failure = "That code is invalid or has expired."
        else:
            expected = _hash_code(code, challenge.salt, challenge.credential_version)
            challenge.attempts += 1
            if not hmac.compare_digest(expected, challenge.code_hash):
                challenge.save(update_fields=["attempts"])
                failure = "That code is invalid or has expired."
            else:
                challenge.consumed_at = now
                challenge.save(update_fields=["consumed_at", "attempts"])
                if purpose == ChallengePurpose.EMAIL_VERIFICATION:
                    locked_user.mark_email_verified()

    # Attempt counters and supersession must survive the failed attempt, so the
    # errors are raised after the transaction commits rather than inside it.
    if too_many:
        raise RateLimited("Too many incorrect codes. Request a new one.")
    if failure is not None:
        raise ValidationFailure(failure)
    return locked_user


class LoginStatus(StrEnum):
    OK = "ok"
    MFA_REQUIRED = "mfa_required"
    UNVERIFIED = "unverified"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class LoginOutcome:
    status: LoginStatus
    user: User | None = None


def login_with_password(*, email: str, password: str, client_ip: str = "") -> LoginOutcome:
    email = (email or "").strip().lower()
    try:
        ratelimit.enforce(
            "login", f"{email}:{client_ip}", limit=10, window_seconds=300
        )
    except RateLimited:
        return LoginOutcome(LoginStatus.RATE_LIMITED)

    user = User.objects.filter(email=email).first()
    if user is None or user.is_client_account:
        # Clients sign in with one-time codes and have no password.
        check_password(password or "", _dummy_hash())
        return LoginOutcome(LoginStatus.FAILED)

    now = timezone.now()
    if user.locked_until and user.locked_until > now:
        return LoginOutcome(LoginStatus.FAILED)

    if not user.check_password(password or ""):
        _record_failed_login(user)
        return LoginOutcome(LoginStatus.FAILED)

    if not user.is_active:
        return LoginOutcome(LoginStatus.FAILED)

    clear_login_failures(user)

    if user.email_verified_at is None:
        try:
            send_challenge(user, ChallengePurpose.EMAIL_VERIFICATION)
        except (RateLimited, ValidationFailure):
            pass
        return LoginOutcome(LoginStatus.UNVERIFIED, user)

    if user.mfa_enabled:
        return LoginOutcome(LoginStatus.MFA_REQUIRED, user)

    return LoginOutcome(LoginStatus.OK, user)


def _record_failed_login(user: User) -> None:
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        locked.failed_login_count += 1
        if locked.failed_login_count >= settings.LOGIN_MAX_FAILURES:
            # Each lockout in a row lasts twice as long as the one before.
            seconds = min(
                settings.LOGIN_LOCK_SECONDS * (2 ** min(locked.login_lockouts, 10)),
                settings.LOGIN_LOCK_MAX_SECONDS,
            )
            locked.locked_until = timezone.now() + timedelta(seconds=seconds)
            locked.failed_login_count = 0
            locked.login_lockouts += 1
            locked.save(update_fields=["failed_login_count", "locked_until", "login_lockouts", "updated_at"])
            audit_service.record(
                action="auth.lockout",
                actor=locked,
                resource_type="user",
                resource_id=str(locked.pk),
                result=AuditEvent.Result.DENIED,
            )
        else:
            locked.save(update_fields=["failed_login_count", "updated_at"])


def clear_login_failures(user: User) -> None:
    if user.failed_login_count or user.locked_until or user.login_lockouts:
        User.objects.filter(pk=user.pk).update(
            failed_login_count=0, locked_until=None, login_lockouts=0, updated_at=timezone.now()
        )
        user.failed_login_count, user.locked_until, user.login_lockouts = 0, None, 0


def establish_session(request, user: User, *, remember: bool = False) -> None:
    django_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["cv"] = user.credential_version
    remember = remember and user.is_client_account
    request.session["remember"] = remember
    request.session.set_expiry(settings.REMEMBER_ME_SECONDS if remember else 0)
    request.session["last_seen"] = int(time.time())
    request.session.cycle_key()
    UserSession.objects.update_or_create(
        session_key=request.session.session_key,
        defaults={
            "user": user,
            "user_agent": (request.META.get("HTTP_USER_AGENT", "") or "")[:200],
            "revoked_at": None,
        },
    )
    audit_service.record(
        action="auth.login",
        actor=user,
        resource_type="session",
        resource_id=request.session.session_key[-8:],
    )


def end_session(request) -> None:
    user = getattr(request, "user", None)
    key = request.session.session_key
    if key:
        UserSession.objects.filter(session_key=key, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
    if user is not None and user.is_authenticated:
        audit_service.record(
            action="auth.logout", actor=user, resource_type="session", resource_id=key[-8:]
        )
    django_logout(request)


def _end_django_sessions(keys) -> None:
    """Actually end the sessions, not just mark them: the device is signed out on its next request."""
    from django.contrib.sessions.models import Session

    Session.objects.filter(session_key__in=list(keys)).delete()


def revoke_session(user: User, session_key: str) -> None:
    if not UserSession.objects.filter(user=user, session_key=session_key).exists():
        return  # only your own sessions
    # One transaction: if ending the session fails, the device stays listed and can be retried.
    with transaction.atomic():
        UserSession.objects.filter(user=user, session_key=session_key).update(
            revoked_at=timezone.now()
        )
        _end_django_sessions([session_key])
    audit_service.record(
        action="auth.session_revoked",
        actor=user,
        resource_type="session",
        resource_id=session_key[-8:],
    )


def revoke_other_sessions(user: User, keep_session_key: str) -> int:
    with transaction.atomic():
        keys = list(
            UserSession.objects.select_for_update().filter(user=user, revoked_at__isnull=True)
            .exclude(session_key=keep_session_key).values_list("session_key", flat=True)
        )
        UserSession.objects.filter(session_key__in=keys).update(revoked_at=timezone.now())
        _end_django_sessions(keys)
    audit_service.record(action="auth.other_sessions_revoked", actor=user, resource_type="user",
                         resource_id=str(user.pk), metadata={"count": len(keys)})
    return len(keys)


def _password_reused(user: User, new_password: str) -> bool:
    for entry in PasswordHistory.objects.filter(user=user)[:PASSWORD_HISTORY_DEPTH]:
        if check_password(new_password, entry.password_hash):
            return True
    return False


def _validate_new_password(user: User, new_password: str) -> None:
    try:
        validate_password(new_password, user=user)
    except ValidationError as exc:
        raise ValidationFailure(" ".join(exc.messages)) from exc
    if user.check_password(new_password):
        raise ValidationFailure("Choose a password you have not used before.")
    if _password_reused(user, new_password):
        raise ValidationFailure("Choose a password you have not used before.")


def change_password(
    user: User, *, current_password: str, new_password: str, request=None
) -> None:
    if not user.check_password(current_password or ""):
        raise ValidationFailure("Your current password is not correct.")
    _validate_new_password(user, new_password)

    now = timezone.now()
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        old_hash = locked.password
        locked.set_password(new_password)
        locked.credential_version += 1
        locked.failed_login_count = 0
        locked.locked_until = None
        locked.save(
            update_fields=[
                "password",
                "credential_version",
                "failed_login_count",
                "locked_until",
                "updated_at",
            ]
        )
        PasswordHistory.objects.create(user=locked, password_hash=old_hash)
        VerificationChallenge.objects.filter(
            user=locked, consumed_at__isnull=True
        ).update(superseded_at=now)
        current_key = request.session.session_key if request else None
        UserSession.objects.filter(user=locked, revoked_at__isnull=True).exclude(
            session_key=current_key or ""
        ).update(revoked_at=now)

    user.refresh_from_db(fields=["credential_version", "password"])
    if request is not None:
        request.session["cv"] = user.credential_version
        request.session["step_up_at"] = int(time.time())
        request.session["step_up_cv"] = user.credential_version
    audit_service.record(
        action="auth.password_changed",
        actor=user,
        resource_type="user",
        resource_id=str(user.pk),
    )


def reset_password(user: User, new_password: str) -> None:
    _validate_new_password(user, new_password)
    now = timezone.now()
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        old_hash = locked.password
        locked.set_password(new_password)
        locked.credential_version += 1
        locked.failed_login_count = 0
        locked.locked_until = None
        locked.save(
            update_fields=[
                "password",
                "credential_version",
                "failed_login_count",
                "locked_until",
                "updated_at",
            ]
        )
        PasswordHistory.objects.create(user=locked, password_hash=old_hash)
        VerificationChallenge.objects.filter(
            user=locked, consumed_at__isnull=True
        ).update(superseded_at=now)
        UserSession.objects.filter(user=locked, revoked_at__isnull=True).update(
            revoked_at=now
        )
    audit_service.record(
        action="auth.password_reset",
        actor=user,
        resource_type="user",
        resource_id=str(user.pk),
    )


# --- MFA ------------------------------------------------------------------


def begin_mfa_enrollment(user: User) -> str:
    secret = mfa.new_secret()
    User.objects.filter(pk=user.pk).update(
        mfa_secret_encrypted=mfa.encrypt_secret(secret),
        mfa_enabled=False,
        mfa_confirmed_at=None,
    )
    return secret


def confirm_mfa_enrollment(user: User, code: str) -> None:
    user.refresh_from_db(fields=["mfa_secret_encrypted", "mfa_last_timestep"])
    if not user.mfa_secret_encrypted:
        raise ValidationFailure("Start enrolment again to get a new secret.")
    secret = mfa.decrypt_secret(user.mfa_secret_encrypted)
    step = mfa.verify_code(secret, code, last_timestep=user.mfa_last_timestep)
    if step is None:
        raise ValidationFailure("That authenticator code is not valid.")
    with transaction.atomic():
        User.objects.filter(pk=user.pk).update(
            mfa_enabled=True,
            mfa_confirmed_at=timezone.now(),
            mfa_last_timestep=step,
        )
    audit_service.record(
        action="auth.mfa_enrolled",
        actor=user,
        resource_type="user",
        resource_id=str(user.pk),
    )


def verify_mfa(user: User, code: str) -> bool:
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        if not locked.mfa_enabled or not locked.mfa_secret_encrypted:
            return False
        secret = mfa.decrypt_secret(locked.mfa_secret_encrypted)
        step = mfa.verify_code(secret, code, last_timestep=locked.mfa_last_timestep)
        if step is None:
            audit_service.record(
                action="auth.mfa_failed",
                actor=locked,
                resource_type="user",
                resource_id=str(locked.pk),
                result=AuditEvent.Result.DENIED,
            )
            return False
        locked.mfa_last_timestep = step
        locked.save(update_fields=["mfa_last_timestep", "updated_at"])
    return True


def verify_step_up_password(user: User, password: str) -> bool:
    if not user.check_password(password or ""):
        return False
    return True


# --- Step-up --------------------------------------------------------------


def mark_step_up(request, user: User) -> None:
    request.session["step_up_at"] = int(time.time())
    request.session["step_up_cv"] = user.credential_version


def has_fresh_step_up(request, user: User) -> bool:
    stamped = request.session.get("step_up_at")
    version = request.session.get("step_up_cv")
    if not stamped or version != user.credential_version:
        return False
    return (int(time.time()) - int(stamped)) <= settings.STEP_UP_MAX_AGE


def request_fingerprint(**fields) -> str:
    joined = "|".join(f"{key}={fields[key]}" for key in sorted(fields))
    return fingerprint(joined)
