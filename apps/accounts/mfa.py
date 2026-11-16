"""TOTP helpers and at-rest protection for MFA secrets.

Uses pyotp for the TOTP algorithm (RFC 6238) and Fernet (AES128-CBC + HMAC) for
encryption at rest. The key is derived from DJANGO_SECRET_KEY, so rotating the
secret key invalidates enrolments; that limitation is documented in
docs/SECURITY.md and is preferable to storing plaintext seeds.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from apps.core.exceptions import ValidationFailure


def _fernet() -> Fernet:
    digest = hashlib.sha256(
        f"{settings.SECRET_KEY}:{settings.MFA_ENC_CONTEXT}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


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
