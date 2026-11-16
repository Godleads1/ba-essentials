"""Sending one-time codes by SMS and WhatsApp through Termii.

The API key stays on the server. Codes are never logged. SMS goes on the DND
route so codes reach numbers that block promotional messages; WhatsApp codes use
Termii's WhatsApp OTP route, which Termii switches on per account.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger("ba.accounts")
TIMEOUT_SECONDS = 10


class TermiiError(Exception):
    pass


def sms_enabled() -> bool:
    return bool(settings.TERMII_API_KEY and settings.TERMII_BASE_URL and settings.TERMII_SENDER_ID)


def whatsapp_enabled() -> bool:
    return sms_enabled() and bool(settings.TERMII_WHATSAPP_FROM)


def _request(payload: dict) -> dict:
    base = settings.TERMII_BASE_URL.rstrip("/")
    if not base.startswith("https://"):
        raise TermiiError("Termii is not configured.")
    request = urllib.request.Request(
        f"{base}/api/sms/send",
        data=json.dumps({**payload, "api_key": settings.TERMII_API_KEY}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # https only, checked above
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise TermiiError(f"Termii returned {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TermiiError("Termii could not be reached.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TermiiError("Termii sent an unreadable answer.") from exc
    if not isinstance(data, dict) or str(data.get("code", "")).lower() != "ok":
        raise TermiiError("Termii did not accept the message.")
    return data


def send_sms(to_e164: str, text: str) -> None:
    if not sms_enabled():
        raise TermiiError("SMS is not set up.")
    _request({
        "to": to_e164.lstrip("+"), "from": settings.TERMII_SENDER_ID, "sms": text,
        "type": "plain", "channel": settings.TERMII_SMS_CHANNEL,
    })


def send_whatsapp_code(to_e164: str, code: str) -> None:
    if not whatsapp_enabled():
        raise TermiiError("WhatsApp is not set up.")
    _request({
        "to": to_e164.lstrip("+"), "from": settings.TERMII_WHATSAPP_FROM, "sms": code,
        "type": "plain", "channel": settings.TERMII_WHATSAPP_CHANNEL,
    })
