"""Phone numbers are stored and compared in one format, E.164 (+2348031234567)."""
from __future__ import annotations

import phonenumbers

from apps.core.exceptions import ValidationFailure

DEFAULT_REGION = "NG"


def normalise(raw: str, region: str = DEFAULT_REGION) -> str:
    """Return the E.164 form, or raise ValidationFailure. Local numbers default to Nigeria."""
    value = (raw or "").strip()
    if not value:
        raise ValidationFailure("Enter a phone number.")
    try:
        number = phonenumbers.parse(value, (region or DEFAULT_REGION).upper())
    except phonenumbers.NumberParseException as exc:
        raise ValidationFailure("Enter a valid phone number, for example 0803 123 4567 or +44 7700 900123.") from exc
    if not phonenumbers.is_valid_number(number):
        raise ValidationFailure("Enter a valid phone number, for example 0803 123 4567 or +44 7700 900123.")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def region(e164: str) -> str:
    """The ISO country code of a stored number ("NG"), or "" if it can't be read."""
    try:
        return phonenumbers.region_code_for_number(phonenumbers.parse(e164 or "", None)) or ""
    except phonenumbers.NumberParseException:
        return ""


def text_countries_label() -> str:
    """The countries codes can go to by text, by name, for messages: "Nigeria"."""
    from django.conf import settings

    from apps.core.countries import BY_CODE

    names = [BY_CODE.get(code, (code,))[0] for code in getattr(settings, "TEXT_COUNTRIES", ["NG"])]
    return ", ".join(names) or "no countries"


def looks_like_phone(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and "@" not in value and sum(c.isdigit() for c in value) >= 7


def masked(e164: str) -> str:
    """+2348031234567 → +234 ••• ••• 4567, for showing where a code went."""
    if not e164:
        return ""
    return f"{e164[:4]} ••• ••• {e164[-4:]}"


def masked_email(email: str) -> str:
    name, _, domain = (email or "").partition("@")
    if not domain:
        return ""
    return f"{name[:2]}{'•' * max(1, min(6, len(name) - 2))}@{domain}"
