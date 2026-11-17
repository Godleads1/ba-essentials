"""Minimal Flutterwave client: start a checkout, confirm a transaction, read a rate.

Server-side only. The secret key comes from settings (an environment variable)
and is never sent to the browser, logged or stored. Standard library only, like
the Paystack client. Tests replace `_request`.

Flutterwave amounts are in major units (dollars, not cents). This module converts
at its edges so the rest of the app always works in minor units.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings

logger = logging.getLogger("ba.billing")

API_BASE = "https://api.flutterwave.com/v3"
TIMEOUT_SECONDS = 15
# Flutterwave's hosted checkout. Any other address is refused.
CHECKOUT_HOSTS = frozenset({"checkout.flutterwave.com", "checkout-v3.flutterwave.com"})
# A naira rate outside this band is treated as a bad answer, not used for pricing.
RATE_BOUNDS = (Decimal(100), Decimal(20000))


class FlutterwaveError(Exception):
    """Flutterwave refused the request or answered with something unusable."""


class FlutterwaveUnavailable(FlutterwaveError):
    """Flutterwave couldn't be reached. Safe to retry later."""


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    secret = settings.FLUTTERWAVE_SECRET_KEY
    if not secret:
        raise FlutterwaveError("Flutterwave is not configured.")
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
        # 5xx and 429 (rate limited) are temporary: retry later.
        if exc.code >= 500 or exc.code == 429:
            raise FlutterwaveUnavailable(f"Flutterwave returned {exc.code}.") from exc
        raise FlutterwaveError(f"Flutterwave returned {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FlutterwaveUnavailable("Flutterwave could not be reached.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FlutterwaveError("Flutterwave sent an unreadable answer.") from exc
    if not isinstance(data, dict) or data.get("status") != "success":
        raise FlutterwaveError("Flutterwave did not accept the request.")
    result = data.get("data")
    if not isinstance(result, dict):
        raise FlutterwaveError("Flutterwave sent no data.")
    return result


def to_major(amount_minor: int) -> str:
    return str((Decimal(amount_minor) / 100).quantize(Decimal("0.01")))


def to_minor(amount) -> int | None:
    try:
        return int((Decimal(str(amount)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return None


def initialize(
    *, email: str, amount_minor: int, currency: str, reference: str, callback_url: str, metadata: dict
) -> str:
    """Start a hosted checkout and return its address."""
    result = _request(
        "POST",
        "/payments",
        {
            "tx_ref": reference,
            "amount": to_major(amount_minor),
            "currency": currency,
            "redirect_url": callback_url,
            "payment_options": "card",
            "customer": {"email": email},
            "meta": {k: str(v)[:100] for k, v in metadata.items()},
            "customizations": {"title": "B & A Essentials"},
        },
    )
    link = str(result.get("link") or "")
    parsed = urllib.parse.urlparse(link)
    if parsed.scheme != "https" or parsed.hostname not in CHECKOUT_HOSTS:
        raise FlutterwaveError("Flutterwave returned an unexpected checkout address.")
    return link


def verify(reference: str) -> dict:
    """Flutterwave's own record of a payment, in the same shape as `paystack.verify`."""
    query = urllib.parse.urlencode({"tx_ref": reference})
    result = _request("GET", f"/transactions/verify_by_reference?{query}")
    card = result.get("card") if isinstance(result.get("card"), dict) else {}
    customer = result.get("customer") if isinstance(result.get("customer"), dict) else {}
    fee = to_minor(result.get("app_fee")) if result.get("app_fee") is not None else None
    return {
        # Flutterwave says "successful"; the shared payment code expects "success".
        "status": "success" if result.get("status") == "successful" else str(result.get("status") or ""),
        "reference": str(result.get("tx_ref") or ""),
        "amount": to_minor(result.get("amount")),
        "currency": str(result.get("currency") or ""),
        "email": str(customer.get("email") or ""),
        "fees": fee,
        "card_country": str(card.get("country") or "")[:2],
        # A reusable card token for renewals (Terms 5.5.3). Kept only server-side.
        "card": {
            "token": str(card.get("token") or ""),
            "reusable": bool(card.get("token")),
            "last4": str(card.get("last_4digits") or "")[-4:],
            "brand": str(card.get("type") or "")[:32].strip().title(),
            "exp_month": _expiry(card.get("expiry"))[0],
            "exp_year": _expiry(card.get("expiry"))[1],
            "bank": str(card.get("issuer") or "")[:80],
        },
    }


def valid_rate(rate) -> bool:
    try:
        rate = Decimal(str(rate))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return rate.is_finite() and RATE_BOUNDS[0] <= rate <= RATE_BOUNDS[1]


def ngn_per_usd() -> Decimal:
    """Today's naira price of one US dollar, from Flutterwave's rate service.

    Asked as US dollars to naira. Flutterwave states a rate as source per
    destination in some answers and destination per source in others, so a value
    below 1 (dollars per naira) is turned the right way up before the bounds check.
    """
    query = urllib.parse.urlencode({"amount": "1", "source_currency": "USD", "destination_currency": "NGN"})
    result = _request("GET", f"/transfers/rates?{query}")
    try:
        rate = Decimal(str(result.get("rate")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FlutterwaveError("Flutterwave sent no usable rate.") from exc
    if not rate.is_finite() or rate <= 0:
        raise FlutterwaveError("Flutterwave sent no usable rate.")
    if rate < 1:
        rate = Decimal(1) / rate
    if not valid_rate(rate):
        raise FlutterwaveError("Flutterwave's rate is outside the expected range.")
    return rate.quantize(Decimal("0.01"))


def _expiry(value) -> tuple[int | None, int | None]:
    """'09/32' -> (9, 2032)."""
    try:
        month, year = str(value).split("/", 1)
        month, year = int(month), int(year)
        return month, (2000 + year if year < 100 else year)
    except (TypeError, ValueError):
        return None, None


