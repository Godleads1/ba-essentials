"""Plan prices by region and billing interval. Every price the app charges comes from here.

- Nigeria: the naira list price, paid with Paystack.
- Rest of Africa: (naira price + surcharge) converted at the day's rate, in US dollars,
  paid with Flutterwave. The rate used is stored on the invoice.
- Outside Africa: the US dollar list price, paid with Flutterwave.
- Yearly: twelve months less the super admin's discount.

Amounts are minor units: kobo for NGN, cents for USD.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from django.core.cache import cache

from apps.core.countries import REGION_AFRICA, REGION_NIGERIA
from apps.core.exceptions import ValidationFailure

from .models import BillingInterval, BillingSettings, Plan

RATE_CACHE_KEY = "billing:fx:ngn_per_usd"
RATE_CACHE_SECONDS = 60 * 60
# After a failed lookup, don't ask again for a minute, so one page doesn't wait on it per plan.
RATE_FAILURE_KEY = "billing:fx:failed"
RATE_FAILURE_CACHE_SECONDS = 60


@dataclass(frozen=True)
class Price:
    amount_minor: int
    currency: str
    interval: str
    rate: Decimal | None = None  # naira per dollar, for rest-of-Africa prices

    @property
    def provider(self) -> str:
        return provider_for(self.currency)


def provider_for(currency: str) -> str:
    return "paystack" if currency == "NGN" else "flutterwave"


def currency_for(region: str) -> str:
    return "NGN" if region == REGION_NIGERIA else "USD"


def ngn_per_usd() -> Decimal:
    """Today's rate: Flutterwave's, cached for an hour; else the admin fallback; else stop."""
    from . import flutterwave

    cached = cache.get(RATE_CACHE_KEY)
    if cached:
        return Decimal(cached)
    rate = None
    if not cache.get(RATE_FAILURE_KEY):
        try:
            rate = flutterwave.ngn_per_usd()
        except flutterwave.FlutterwaveError:
            cache.set(RATE_FAILURE_KEY, "1", RATE_FAILURE_CACHE_SECONDS)
    if rate is not None:
        cache.set(RATE_CACHE_KEY, str(rate), RATE_CACHE_SECONDS)
        return rate
    fallback = BillingSettings.current().fallback_ngn_per_usd
    # The fallback is only used if it's a believable rate, never as a raw divisor.
    if fallback and flutterwave.valid_rate(fallback):
        return Decimal(fallback)
    raise ValidationFailure(
        "We couldn't get today's exchange rate. Please try again in a few minutes."
    )


def _round(amount: Decimal, unit: int) -> int:
    """Round up to a whole `unit` of minor units (100 = whole naira or whole dollars)."""
    return int((amount / unit).to_integral_value(rounding=ROUND_CEILING)) * unit


def monthly_minor(plan: Plan, region: str, rate: Decimal | None = None) -> tuple[int, Decimal | None]:
    if region == REGION_NIGERIA:
        return plan.price_minor, None
    if region == REGION_AFRICA:
        rate = rate or ngn_per_usd()
        surcharge = BillingSettings.current().africa_surcharge_ngn_minor
        # kobo / (naira per dollar) = cents, rounded up to the cent
        cents = Decimal(plan.price_minor + surcharge) / rate
        return int(cents.to_integral_value(rounding=ROUND_CEILING)), rate
    if plan.price_usd_minor <= 0:
        raise ValidationFailure("That plan isn't available in US dollars yet.")
    return plan.price_usd_minor, None


def plan_price(plan: Plan, region: str, interval: str = BillingInterval.MONTH, *, rate=None) -> Price:
    monthly, rate = monthly_minor(plan, region, rate)
    currency = currency_for(region)
    if interval != BillingInterval.YEAR:
        return Price(monthly, currency, BillingInterval.MONTH, rate)
    discount = BillingSettings.current().yearly_discount_pct
    year = Decimal(monthly * 12) * (Decimal(100 - discount) / 100)
    # Whole naira or whole dollars, except converted rest-of-Africa prices, which keep cents.
    unit = 1 if region == REGION_AFRICA else 100
    amount = _round(year, unit) if unit > 1 else int(year.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return Price(amount, currency, BillingInterval.YEAR, rate)


def extra_legal_fee(region: str) -> tuple[int, str]:
    settings = BillingSettings.current()
    if region == REGION_NIGERIA:
        return settings.extra_legal_fee_ngn_minor, "NGN"
    return settings.extra_legal_fee_usd_minor, "USD"
