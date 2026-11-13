"""Minimal Paystack client: start a checkout and confirm a transaction.

Server-side only. The secret key comes from settings (an environment variable)
and is never sent to the browser, logged or stored. Standard library only, so
there is no extra dependency to vet. Tests replace `_request`.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger("ba.billing")

API_BASE = "https://api.paystack.co"
TIMEOUT_SECONDS = 15
# Paystack's hosted checkout. A checkout address anywhere else is refused, so a
# tampered or unexpected API answer can't send a client to another site.
CHECKOUT_HOSTS = frozenset({"checkout.paystack.com"})


class PaystackError(Exception):
    """Paystack refused the request or answered with something unusable."""


class PaystackUnavailable(PaystackError):
    """Paystack couldn't be reached. Safe to retry later."""


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    secret = settings.PAYSTACK_SECRET_KEY
    if not secret:
        raise PaystackError("Paystack is not configured.")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        API_BASE + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # fixed https host (API_BASE)
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 4xx: Paystack rejected it (don't retry); 5xx: their side (retry).
        if exc.code >= 500:
            raise PaystackUnavailable(f"Paystack returned {exc.code}.") from exc
        raise PaystackError(f"Paystack returned {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PaystackUnavailable("Paystack could not be reached.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaystackError("Paystack sent an unreadable answer.") from exc
    if not isinstance(data, dict) or data.get("status") is not True:
        raise PaystackError("Paystack did not accept the request.")
    result = data.get("data")
    if not isinstance(result, dict):
        raise PaystackError("Paystack sent an incomplete answer.")
    return result


def initialize(
    *, email: str, amount_minor: int, currency: str, reference: str, callback_url: str,
    metadata: dict,
) -> str:
    """Start a hosted checkout and return the address to send the client to."""
    result = _request(
        "POST",
        "/transaction/initialize",
        {
            "email": email,
            "amount": amount_minor,
            "currency": currency,
            "reference": reference,
            "callback_url": callback_url,
            "metadata": metadata,
        },
    )
    url = str(result.get("authorization_url") or "")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in CHECKOUT_HOSTS:
        raise PaystackError("Paystack sent an unexpected checkout address.")
    if result.get("reference") not in (None, reference):
        raise PaystackError("Paystack changed the payment reference.")
    return url


def verify(reference: str) -> dict:
    """Ask Paystack directly what happened to a transaction.

    Returns the fields billing compares against its own record. The webhook
    alone is never enough to activate anything.
    """
    result = _request("GET", "/transaction/verify/" + urllib.parse.quote(reference, safe=""))
    customer = result.get("customer") if isinstance(result.get("customer"), dict) else {}
    return {
        "status": str(result.get("status") or ""),
        "reference": str(result.get("reference") or ""),
        "amount": result.get("amount"),
        "currency": str(result.get("currency") or ""),
        "email": str(customer.get("email") or ""),
    }
