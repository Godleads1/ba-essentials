"""Refuse staff passwords that have appeared in data breaches (security checklist 9).

Uses the Pwned Passwords range API by k-anonymity: the password is hashed with SHA-1 and
only the first 5 of the 40 hex characters leave the server. The service answers with every
breached hash that starts with them (padded with decoys, so the answer's size gives nothing
away), and the match is checked here. The password itself is never sent or logged.

If the service can't be reached, the password is allowed and a warning is logged: the other
validators (12+ characters, not a common password) still apply, and staff accounts also need
an authenticator code to sign in.
"""
from __future__ import annotations

import hashlib
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.exceptions import ValidationError

logger = logging.getLogger("ba.accounts")

RANGE_URL = "https://api.pwnedpasswords.com/range/"
TIMEOUT_SECONDS = 3
MESSAGE = ("This password has appeared in a data breach, so attackers try it first. "
           "Choose a different one, such as three or four random words.")


def _fetch(prefix: str) -> str:
    request = urllib.request.Request(
        RANGE_URL + prefix,
        headers={"Add-Padding": "true", "User-Agent": "banda-password-check"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # fixed https host
        return response.read().decode("utf-8")


def times_breached(password: str) -> int | None:
    """How many breaches the password is in (0 if none), or None if the service didn't answer."""
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()  # the API's format
    prefix, suffix = digest[:5], digest[5:]
    try:
        body = _fetch(prefix)
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError):
        logger.warning("breached_password_check_unavailable")
        return None
    for line in body.splitlines():
        found, _, count = line.strip().partition(":")
        if found == suffix:
            try:
                return int(count)  # padding lines have a count of 0
            except ValueError:
                return 0
    return 0


class BreachedPasswordValidator:
    def validate(self, password, user=None):
        if not getattr(settings, "BREACHED_PASSWORD_CHECK", True):
            return
        if (times_breached(password) or 0) > 0:
            raise ValidationError(MESSAGE, code="password_breached")

    def get_help_text(self):
        return "Your password can't be one that has appeared in a known data breach."
