"""Client sign-in with one-time codes (guide section 4.1).

Clients have no password. They get a 6-digit code by WhatsApp, SMS or email.

- Codes are random, stored only as an HMAC, single use, bound to the account and
  the purpose, expire after OTP_TTL_SECONDS, and a new code cancels the old one
  (all in services.issue_challenge / verify_challenge).
- Sends are limited per phone number or email and per IP address; wrong codes are
  limited per code and per account, with lockouts that double each time.
- The answer never shows whether an account exists: the same message, the same
  next page, and a minimum response time.
- A phone number becomes a way to sign in only after a code sent to it has been
  entered, so nobody can put someone else's number on their account.
- Codes never go into logs, URLs or error reports.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit_service
from apps.core import ratelimit
from apps.core.exceptions import RateLimited, ValidationFailure

from . import phones, services, termii
from .models import ChallengePurpose, CodeChannel, User, VerificationChallenge

logger = logging.getLogger("ba.accounts")

SENDS_PER_DESTINATION_PER_HOUR = 5
SENDS_PER_IP_PER_HOUR = 20
CHECKS_PER_IP_PER_10_MIN = 30
CODE_RECORD_HOURS = 24  # the Privacy Policy keeps code records for at most a day
INVALID = "That code is invalid or has expired."


def phone_channels() -> list[str]:
    """Phone channels this server can send on, WhatsApp first."""
    out = []
    if termii.whatsapp_enabled():
        out.append(CodeChannel.WHATSAPP)
    if termii.sms_enabled():
        out.append(CodeChannel.SMS)
    return out


def texts_possible(phone: str) -> bool:
    """Codes can go to this number by text or WhatsApp right now (depends only on the number
    and the site-wide limits, never on whether an account exists)."""
    return bool(phone_channels()) and termii.country_allowed(phone) and not termii.cap_reached()


def _abroad_message() -> str:
    return (f"We can only send codes by text or WhatsApp to numbers in {phones.text_countries_label()}. "
            "Use your email address instead.")


def channels_for(user: User) -> list[str]:
    allowed = user.phone_verified and termii.country_allowed(user.phone) and not termii.cap_reached()
    out = list(phone_channels()) if allowed else []
    if user.email:
        out.append(CodeChannel.EMAIL)
    return out


def preferred_channel(user: User) -> str:
    available = channels_for(user)
    return user.code_channel if user.code_channel in available else available[0]


@contextmanager
def _minimum_time():
    """Pad the response so a real send and no send take the same time."""
    started = time.monotonic()
    try:
        yield
    finally:
        left = settings.AUTH_MIN_RESPONSE_SECONDS - (time.monotonic() - started)
        if left > 0:
            time.sleep(left)


def _limit_sends(destination: str, client_ip: str) -> None:
    if not ratelimit.hit("code-ip", client_ip or "-", limit=SENDS_PER_IP_PER_HOUR, window_seconds=3600):
        raise RateLimited("Too many codes have been requested from here. Please wait an hour and try again.")
    if not ratelimit.hit("code-dest", destination, limit=SENDS_PER_DESTINATION_PER_HOUR, window_seconds=3600):
        raise RateLimited("Too many codes have been sent to this address or number. Please wait an hour and try again.")


def _text(purpose: str, code: str) -> str:
    minutes = max(1, settings.OTP_TTL_SECONDS // 60)
    what = {
        ChallengePurpose.PHONE_VERIFICATION: "to confirm your phone number",
        ChallengePurpose.STEP_UP: "to confirm it's you",
        ChallengePurpose.PHONE_CHANGE: "to confirm your new phone number",
    }.get(purpose, "to sign in")
    return f"Your B&A Essentials code {what} is {code}. It expires in {minutes} minutes. Never share it."


def send_code(user: User, purpose: str, channel: str, *, phone: str = "", email: str = "") -> str:
    """Issue a code and send it. Returns where it went, masked. The code is never kept in plain text."""
    phone = phone or user.phone
    if channel in (CodeChannel.SMS, CodeChannel.WHATSAPP) and not (phone and channel in phone_channels()):
        raise ValidationFailure("Codes can't be sent that way right now. Choose another way.")
    destination = (email or user.email) if channel == CodeChannel.EMAIL else phone
    challenge = services.issue_challenge(user, purpose, channel=channel, target=destination)
    code = challenge.plaintext_code  # type: ignore[attr-defined]
    del challenge.plaintext_code
    try:
        if channel == CodeChannel.EMAIL:
            services._send_code_email(user, purpose, code, to=email)
            return phones.masked_email(email or user.email)
        if channel == CodeChannel.SMS:
            termii.send_sms(phone, _text(purpose, code))
        else:
            termii.send_whatsapp_code(phone, code)
        return phones.masked(phone)
    except (termii.TermiiError, ValidationFailure) as exc:
        VerificationChallenge.objects.filter(pk=challenge.pk).update(superseded_at=timezone.now())
        logger.warning("code_send_failed channel=%s purpose=%s user=%s", channel, purpose, user.pk)
        how = "email" if channel == CodeChannel.EMAIL else ("WhatsApp" if channel == CodeChannel.WHATSAPP else "text message")
        raise ValidationFailure(f"We couldn't send your code by {how} just now. Try again, or choose another way.") from exc


# ---------- signing in ----------

def _parse_identifier(identifier: str) -> tuple[str, str]:
    value = (identifier or "").strip()
    if "@" in value:
        return "email", value.lower()
    if not phone_channels():
        raise ValidationFailure("Enter your email address. Codes by phone aren't available yet.")
    phone = phones.normalise(value)
    if not termii.country_allowed(phone):
        raise ValidationFailure(_abroad_message())
    if termii.cap_reached():
        raise ValidationFailure("Codes by text or WhatsApp aren't available right now. Enter your email address instead.")
    return "phone", phone


def _find_client(kind: str, key: str) -> User | None:
    if kind == "email":
        user = User.objects.filter(email=key, is_active=True).first()
    else:
        user = User.objects.filter(phone=key, phone_verified_at__isnull=False, is_active=True).first()
    if user is None or not user.is_client_account:
        return None
    if user.locked_until and user.locked_until > timezone.now():
        return None
    return user


def request_sign_in(identifier: str, *, client_ip: str = "", channel: str = "") -> dict:
    """Send a sign-in code if the account exists. Always returns the same kind of answer.

    The result is kept in the session to drive the code page. It holds the account
    id only when one was found, and never the code.
    """
    kind, key = _parse_identifier(identifier)
    if kind == "email":
        channel = CodeChannel.EMAIL
    elif channel not in phone_channels():
        channel = phone_channels()[0]
    pending = {
        "kind": kind, "key": key, "ch": channel, "uid": "",
        "to": phones.masked_email(key) if kind == "email" else phones.masked(key),
    }
    with _minimum_time():
        _limit_sends(key, client_ip)
        user = _find_client(kind, key)
        if user is None:
            services._hash_code("000000", "no-account", 1)  # similar work either way
            return pending
        pending["uid"] = str(user.pk)
        try:
            send_code(user, ChallengePurpose.SIGN_IN, channel, phone=key if kind == "phone" else "")
        except RateLimited:
            pass  # the last code is still valid; same answer
        except ValidationFailure:
            pass  # logged in send_code; showing it would reveal the account
    return pending


def verify_sign_in(pending: dict, code: str, *, client_ip: str = "") -> User:
    if not ratelimit.hit("code-check-ip", client_ip or "-", limit=CHECKS_PER_IP_PER_10_MIN, window_seconds=600):
        raise RateLimited("Too many attempts. Please wait a few minutes and try again.")
    user = User.objects.filter(pk=pending.get("uid") or None, is_active=True).first() if pending.get("uid") else None
    if user is None or not user.is_client_account:
        services._hash_code(code or "", "no-account", 1)
        raise ValidationFailure(INVALID)
    if user.locked_until and user.locked_until > timezone.now():
        raise ValidationFailure(INVALID)
    try:
        user = services.verify_challenge(user, ChallengePurpose.SIGN_IN, code)
    except ValidationFailure:
        services._record_failed_login(user)
        raise
    if pending.get("kind") == "email":
        user.mark_email_verified()
    services.clear_login_failures(user)
    return user


# ---------- signing up ----------

def register_client(*, full_name: str, email: str, phone: str = "", channel: str = "", client_ip: str = "") -> dict:
    """Start an account. The first code always goes to the email address.

    If the email already has an account, this sends that account a sign-in code
    instead, with the same answer, so sign-up never reveals who has an account.
    A phone number given here is confirmed by a second code after the email.
    """
    if settings.SIGNUPS_PAUSED:
        raise ValidationFailure("We're not opening new accounts just now. Please try again later.")
    email = (email or "").strip().lower()
    full_name = (full_name or "").strip()
    phone = phones.normalise(phone) if (phone or "").strip() else ""
    if channel in (CodeChannel.SMS, CodeChannel.WHATSAPP) and not phone:
        raise ValidationFailure("Add your phone number to get codes by WhatsApp or text message.")
    if channel not in channels_offered_at_signup() or (phone and not termii.country_allowed(phone)):
        channel = CodeChannel.EMAIL  # the number is kept; codes go by email
    pending = {"kind": "email", "key": email, "ch": CodeChannel.EMAIL, "uid": "", "to": phones.masked_email(email), "signup": True}
    with _minimum_time():
        _limit_sends(email, client_ip)
        user = User.objects.filter(email=email).first()
        if user is not None and not user.is_client_account:
            return pending
        if user is None:
            try:
                with transaction.atomic():
                    user = User(email=email, full_name=full_name, phone=phone, code_channel=channel)
                    user.set_unusable_password()
                    user.save()
            except IntegrityError:
                return pending
        elif user.email_verified_at is None:
            # Nobody has proven this email yet, so the latest sign-up's details win.
            user.full_name, user.phone, user.phone_verified_at, user.code_channel = full_name, phone, None, channel
            user.save(update_fields=["full_name", "phone", "phone_verified_at", "code_channel", "updated_at"])
        pending["uid"] = str(user.pk)
        if user.phone and not user.phone_verified and channel != CodeChannel.EMAIL:
            pending["confirm_phone"] = True
        try:
            send_code(user, ChallengePurpose.SIGN_IN, CodeChannel.EMAIL)
        except (RateLimited, ValidationFailure):
            pass
    return pending


def channels_offered_at_signup() -> list[str]:
    return [*phone_channels(), CodeChannel.EMAIL]


# ---------- confirming a phone number (signed in) ----------

def send_phone_code(user: User, *, client_ip: str = "", channel: str = "") -> str:
    if not user.phone:
        raise ValidationFailure("Add a phone number first.")
    if channel not in phone_channels():
        channel = user.code_channel if user.code_channel in phone_channels() else (phone_channels() or [""])[0]
    if not channel:
        raise ValidationFailure("Codes by phone aren't available yet.")
    if not termii.country_allowed(user.phone):
        raise ValidationFailure(_abroad_message())
    _limit_sends(user.phone, client_ip)
    return send_code(user, ChallengePurpose.PHONE_VERIFICATION, channel)


def confirm_phone(user: User, code: str) -> User:
    user = services.verify_challenge(user, ChallengePurpose.PHONE_VERIFICATION, code, target=user.phone)
    now = timezone.now()
    try:
        with transaction.atomic():
            # Anyone else who typed this number without proving it loses it.
            User.objects.filter(phone=user.phone, phone_verified_at__isnull=True).exclude(pk=user.pk).update(phone="")
            User.objects.filter(pk=user.pk).update(phone_verified_at=now, updated_at=now)
    except IntegrityError as exc:
        raise ValidationFailure("This number is already used to sign in to another account.") from exc
    user.refresh_from_db()
    audit_service.record(action="auth.phone_verified", actor=user, resource_type="user", resource_id=str(user.pk))
    return user


def set_channel(user: User, channel: str) -> None:
    if channel not in channels_for(user):
        raise ValidationFailure("Choose a way we can reach you.")
    User.objects.filter(pk=user.pk).update(code_channel=channel, updated_at=timezone.now())
    audit_service.record(action="auth.code_channel_changed", actor=user, resource_type="user",
                         resource_id=str(user.pk), metadata={"channel": channel})


# ---------- confirming it's you before something sensitive (clients have no password) ----------

def send_step_up_code(user: User, *, client_ip: str = "") -> str:
    _limit_sends(f"step-up:{user.pk}", client_ip)
    return send_code(user, ChallengePurpose.STEP_UP, preferred_channel(user))


def verify_step_up_code(user: User, code: str) -> bool:
    try:
        services.verify_challenge(user, ChallengePurpose.STEP_UP, code)
    except (ValidationFailure, RateLimited):
        return False
    return True


def purge_old_codes() -> int:
    cutoff = timezone.now() - timedelta(hours=CODE_RECORD_HOURS)
    deleted, _ = VerificationChallenge.objects.filter(created_at__lt=cutoff).delete()
    return deleted


# ---------- changing contact details (step 4b) ----------
# The view asks for a fresh "confirm it's you" first. The code goes to the NEW
# email or number, and a notice goes to the old one. A detail another account
# already uses gets the same answer and no code, so this never reveals who has
# an account. Changing either signs out the account's other devices.

def _notify_email(to: str, subject: str, body: str) -> None:
    from django.core.mail import send_mail

    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to])
    except Exception:  # noqa: BLE001 - a notice must never block the change itself
        logger.warning("security_notice_failed subject=%s", subject)


def _notify_phone(phone: str, text: str) -> None:
    if not termii.sms_enabled():
        return
    try:
        termii.send_sms(phone, text)
    except termii.TermiiError:
        logger.warning("security_notice_sms_failed")


def _not_you() -> str:
    return f"If this wasn't you, email {settings.SUPPORT_EMAIL} straight away."


def start_email_change(user: User, new_email: str, *, client_ip: str = "") -> str:
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    new = (new_email or "").strip().lower()
    try:
        validate_email(new)
    except ValidationError as exc:
        raise ValidationFailure("Enter a valid email address, like name@company.com.") from exc
    if new == user.email:
        raise ValidationFailure("That's already your email address.")
    _limit_sends(new, client_ip)
    with _minimum_time():
        if User.objects.filter(email=new).exclude(pk=user.pk).exists():
            _issue_unsent(user, ChallengePurpose.EMAIL_CHANGE, CodeChannel.EMAIL, new)
        else:
            send_code(user, ChallengePurpose.EMAIL_CHANGE, CodeChannel.EMAIL, email=new)
    return phones.masked_email(new)


def _issue_unsent(user: User, purpose: str, channel: str, target: str) -> None:
    """For a detail another account uses: go through the same steps as a real send
    (the resend wait, cancelling the last code) but send nothing, so the answer
    is the same either way."""
    challenge = services.issue_challenge(user, purpose, channel=channel, target=target)
    del challenge.plaintext_code


def confirm_email_change(user: User, new_email: str, code: str) -> User:
    new = (new_email or "").strip().lower()
    user = services.verify_challenge(user, ChallengePurpose.EMAIL_CHANGE, code, target=new)
    old = user.email
    now = timezone.now()
    try:
        with transaction.atomic():
            User.objects.filter(pk=user.pk).update(email=new, email_verified_at=now, updated_at=now)
    except IntegrityError as exc:
        raise ValidationFailure(INVALID) from exc
    user.bump_credential_version()
    user.refresh_from_db()
    audit_service.record(action="auth.email_changed", actor=user, resource_type="user", resource_id=str(user.pk))
    _notify_email(old, "Your B & A Essentials email address was changed",
                  f"The email address on your B & A Essentials account was changed to {phones.masked_email(new)}. "
                  f"From now on, codes and messages go there. {_not_you()}")
    return user


def start_phone_change(user: User, raw_phone: str, *, channel: str = "", client_ip: str = "") -> str:
    if not phone_channels():
        raise ValidationFailure("Codes by phone aren't available yet.")
    new = phones.normalise(raw_phone)
    if new == user.phone and user.phone_verified:
        raise ValidationFailure("That's already your phone number.")
    if not termii.country_allowed(new):
        raise ValidationFailure(_abroad_message())
    if channel not in phone_channels():
        channel = phone_channels()[0]
    _limit_sends(new, client_ip)
    with _minimum_time():
        taken = User.objects.filter(phone=new, phone_verified_at__isnull=False).exclude(pk=user.pk).exists()
        if taken:
            _issue_unsent(user, ChallengePurpose.PHONE_CHANGE, channel, new)
        else:
            try:
                send_code(user, ChallengePurpose.PHONE_CHANGE, channel, phone=new)
            except ValidationFailure:
                raise ValidationFailure("We couldn't send a code to that number just now. Try again, or choose the other way.")
    return phones.masked(new)


def confirm_phone_change(user: User, new_phone: str, code: str) -> User:
    new = phones.normalise(new_phone)
    user = services.verify_challenge(user, ChallengePurpose.PHONE_CHANGE, code, target=new)
    old, old_verified = user.phone, user.phone_verified
    now = timezone.now()
    try:
        with transaction.atomic():
            User.objects.filter(phone=new, phone_verified_at__isnull=True).exclude(pk=user.pk).update(phone="")
            User.objects.filter(pk=user.pk).update(phone=new, phone_verified_at=now, updated_at=now)
    except IntegrityError as exc:
        raise ValidationFailure(INVALID) from exc
    user.bump_credential_version()
    user.refresh_from_db()
    audit_service.record(action="auth.phone_changed", actor=user, resource_type="user", resource_id=str(user.pk))
    if old and old_verified and old != new:
        _notify_phone(old, f"Your B&A Essentials phone number was changed to {phones.masked(new)}. {_not_you()}")
    _notify_email(user.email, "Your B & A Essentials phone number was changed",
                  f"The phone number on your account is now {phones.masked(new)}. You can sign in with it. {_not_you()}")
    return user


def remove_phone(user: User) -> User:
    old, old_verified = user.phone, user.phone_verified
    if not old:
        raise ValidationFailure("There's no phone number to remove.")
    User.objects.filter(pk=user.pk).update(phone="", phone_verified_at=None, code_channel=CodeChannel.EMAIL,
                                           updated_at=timezone.now())
    user.bump_credential_version()
    user.refresh_from_db()
    audit_service.record(action="auth.phone_removed", actor=user, resource_type="user", resource_id=str(user.pk))
    if old_verified:
        _notify_phone(old, f"Your number was removed from your B&A Essentials account. {_not_you()}")
    _notify_email(user.email, "Your phone number was removed",
                  f"Your phone number was removed from your B & A Essentials account. Codes now come by email. {_not_you()}")
    return user


def disable_mfa(user: User) -> User:
    """Clients may turn their optional authenticator off. Staff must keep theirs."""
    if not user.is_client_account:
        raise ValidationFailure("Staff accounts must keep their authenticator app.")
    if not user.mfa_enabled:
        return user
    User.objects.filter(pk=user.pk).update(mfa_enabled=False, mfa_secret_encrypted="", mfa_confirmed_at=None,
                                           mfa_last_timestep=None, updated_at=timezone.now())
    user.bump_credential_version()
    user.refresh_from_db()
    audit_service.record(action="auth.mfa_disabled", actor=user, resource_type="user", resource_id=str(user.pk))
    _notify_email(user.email, "Authenticator app turned off",
                  f"The authenticator app was turned off for your B & A Essentials account. {_not_you()}")
    return user
