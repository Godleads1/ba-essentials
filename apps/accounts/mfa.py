"""TOTP helpers and at-rest protection for MFA secrets.

Uses pyotp for the TOTP algorithm (RFC 6238) and Fernet (AES128-CBC + HMAC) for
encryption at rest. The key is MFA_ENCRYPTION_KEY (a Fernet key), kept apart from
DJANGO_SECRET_KEY so the secret key can be rotated after a leak without breaking every
authenticator. Secrets made before MFA_ENCRYPTION_KEY existed were encrypted with a key
derived from DJANGO_SECRET_KEY; they can still be read, and are re-encrypted with the new
key the next time they are used.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pyotp
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

from apps.core.exceptions import ValidationFailure


def _legacy_fernet() -> Fernet:
    digest = hashlib.sha256(
        f"{settings.SECRET_KEY}:{settings.MFA_ENC_CONTEXT}".encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet() -> MultiFernet:
    """Encrypts with MFA_ENCRYPTION_KEY when set; still reads the older secret-key-derived form."""
    keys = [Fernet(settings.MFA_ENCRYPTION_KEY)] if settings.MFA_ENCRYPTION_KEY else []
    return MultiFernet([*keys, _legacy_fernet()])


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def needs_rotation(token: str) -> bool:
    """True when a stored secret is still under the old key and MFA_ENCRYPTION_KEY is set."""
    if not settings.MFA_ENCRYPTION_KEY:
        return False
    try:
        Fernet(settings.MFA_ENCRYPTION_KEY).decrypt(token.encode("ascii"))
    except (InvalidToken, ValueError):
        return True
    return False


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ValidationFailure(
            "This authenticator could not be read. Reset MFA and enrol again."
        ) from exc


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, *, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(
        name=email, issuer_name=settings.MFA_ISSUER
    )


def _current_timestep() -> int:
    return int(time.time()) // 30


def verify_code(secret: str, code: str, *, last_timestep: int | None) -> int | None:
    """Return the matched 30-second timestep, or None.

    A code whose timestep is not newer than the last accepted one is rejected
    so an observed code cannot be replayed.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    current = _current_timestep()
    for offset in (0, -1, 1):
        step = current + offset
        expected = totp.at(step * 30)
        if hmac.compare_digest(expected, code):
            if last_timestep is not None and step <= last_timestep:
                return None
            return step
    return None
