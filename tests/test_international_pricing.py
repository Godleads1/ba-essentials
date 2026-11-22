"""Prices follow the business's country: naira with Paystack in Nigeria, dollars with
Flutterwave elsewhere (the rest of Africa at the day's rate). Monthly or yearly with the
super admin's discount. Flutterwave payments count only once Flutterwave confirms them."""
from __future__ import annotations

import io
import json
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.test import Client, override_settings

from apps.audit.models import AuditEvent
from apps.billing import pricing
from apps.billing import services as billing
from apps.billing.models import (
    BillingSettings,
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
    Plan,
    Subscription,
)
from apps.core.countries import region_for
from apps.core.exceptions import ValidationFailure

from .conftest import login_client

HASH = "synthetic-flutterwave-hash"


@pytest.fixture
def plans(db):
    call_command("seed_plans", stdout=io.StringIO())
    return {p.slug: p for p in Plan.objects.all()}


def _set_country(organization, code):
    organization.country = code
    organization.save(update_fields=["country"])
    return organization


def _flw_webhook(payload, signature=HASH):
    return Client().post("/webhooks/flutterwave/", json.dumps(payload), content_type="application/json",
                         HTTP_VERIF_HASH=signature)


def _flw_pay(attempt, fake_flutterwave, event_id, **kw):
    fake_flutterwave.confirm(attempt.reference, attempt.amount_minor, currency=attempt.currency, **kw)
    response = _flw_webhook({"event": "charge.completed", "data": {
        "id": event_id, "tx_ref": attempt.reference, "amount": attempt.amount_minor / 100,
        "currency": attempt.currency, "status": "successful"}})
    attempt.refresh_from_db()
    return response


# ---------- regions ----------

@pytest.mark.parametrize("code, region", [("NG", "ng"), ("GH", "africa"), ("KE", "africa"), ("ZA", "africa"),
                                          ("GB", "intl"), ("US", "intl"), ("AE", "intl"), ("", "ng")])
def test_country_sets_the_region(code, region):
    assert region_for(code) == region


# ---------- prices ----------

def test_prices_by_region_and_interval(plans):
    growth = plans["growth"]
    assert pricing.plan_price(growth, "ng").amount_minor == 9_750_000
    assert pricing.plan_price(growth, "intl").amount_minor == 8_900
    # (₦97,500 + ₦100) ÷ ₦1,550 = $62.97 (rounded up to the cent)
    africa = pricing.plan_price(growth, "africa", rate=Decimal(1550))
    assert (africa.amount_minor, africa.currency) == (6_297, "USD")
    # Yearly in naira: the totals in the Terms (5.1), not a formula
    assert pricing.plan_price(growth, "ng", "year").amount_minor == 105_300_000  # ₦1,053,000
    assert pricing.plan_price(plans["starter"], "ng", "year").amount_minor == 66_000_000  # ₦660,000
    assert pricing.plan_price(plans["dedicated"], "ng", "year").amount_minor == 280_800_000  # ₦2,808,000
    # Rest of Africa: the same yearly naira price (+ 12 months' surcharge) at today's rate:
    # (₦1,053,000 + 12 × ₦100) ÷ ₦1,550 = $680.13
    assert pricing.plan_price(growth, "africa", "year", rate=Decimal(1550)).amount_minor == 68_013
    # Outside Africa, US dollars: 15% off, whole dollars
    assert pricing.plan_price(growth, "intl", "year").amount_minor == 90_800  # $908
    assert pricing.plan_price(plans["starter"], "intl", "year").amount_minor == 56_100  # $561


def test_a_plan_without_its_own_yearly_price_uses_the_discount(plans):
    growth = plans["growth"]
    growth.price_year_minor = 0
    growth.save()
    assert pricing.plan_price(growth, "ng", "year").amount_minor == 99_450_000  # 97,500 × 12 × 0.85


def test_saving_labels_follow_the_real_prices():
    assert pricing.yearly_saving_pct(9_750_000, 105_300_000) == 10
    assert pricing.yearly_saving_pct(6_500_000, 66_000_000) == 15  # 15.4%
    assert pricing.saving_label([15, 10, 10]) == "Save up to 15%"
    assert pricing.saving_label([15, 15]) == "Save 15%"
    assert pricing.saving_label([0]) == ""


def test_super_admin_settings_change_prices(plans):
    settings = BillingSettings.current()
    settings.yearly_discount_pct = 20
    settings.africa_surcharge_ngn_minor = 50_000
    settings.save()
    assert pricing.plan_price(plans["growth"], "intl", "year").amount_minor == 85_500  # 89×12×0.8 = 854.4 → $855
    assert pricing.plan_price(plans["growth"], "africa", rate=Decimal(1550)).amount_minor == 6_323  # (97,500+500)/1,550


def test_africa_price_uses_the_live_rate_and_caches_it(plans, fake_flutterwave):
    first = pricing.plan_price(plans["starter"], "africa")
    fake_flutterwave.rate = "1600.00"
    assert pricing.plan_price(plans["starter"], "africa").amount_minor == first.amount_minor  # cached for an hour
    assert first.rate == Decimal("1550.00")


def test_no_rate_and_no_fallback_stops_dollar_checkout(plans, fake_flutterwave):
    fake_flutterwave.rate = None
    with pytest.raises(ValidationFailure, match="exchange rate"):
        pricing.plan_price(plans["starter"], "africa")
    settings = BillingSettings.current()
    settings.fallback_ngn_per_usd = Decimal(1500)
    settings.save()
    assert pricing.plan_price(plans["starter"], "africa").rate == Decimal(1500)


def test_an_implausible_rate_is_refused(plans, fake_flutterwave):
    fake_flutterwave.rate = "3.10"
    with pytest.raises(ValidationFailure):
        pricing.plan_price(plans["starter"], "africa")


# ---------- checkout ----------

@override_settings(PAYMENTS_ENABLED=True)
def test_nigeria_pays_naira_with_paystack(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "NG")
    response = login_client(owner).post("/app/plan/checkout/growth/", {"interval": "month"})
    assert response["Location"].startswith("https://checkout.paystack.com/")
    attempt = PaymentAttempt.objects.get()
    assert (attempt.provider, attempt.currency, attempt.amount_minor) == ("paystack", "NGN", 9_750_000)
    assert not fake_flutterwave.calls


@override_settings(PAYMENTS_ENABLED=True)
def test_uk_pays_dollars_with_flutterwave_and_starts_on_confirmation(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "GB")
    response = login_client(owner).post("/app/plan/checkout/starter/", {"interval": "month"})
    assert response["Location"] == fake_flutterwave.checkout_url
    attempt = PaymentAttempt.objects.select_related("invoice").get()
    assert (attempt.provider, attempt.currency, attempt.amount_minor) == ("flutterwave", "USD", 5_500)
    sent = fake_flutterwave.calls[0][2]
    assert sent["amount"] == "55.00" and sent["currency"] == "USD" and sent["tx_ref"] == attempt.reference
    assert not Subscription.objects.filter(organization=organization).exists()
    assert _flw_pay(attempt, fake_flutterwave, 501).status_code == 200
    sub = Subscription.objects.get(organization=organization)
    assert sub.plan.slug == "starter" and sub.interval == "month"
    assert Invoice.objects.get(pk=attempt.invoice_id).status == InvoiceStatus.PAID


@override_settings(PAYMENTS_ENABLED=True)
def test_ghana_pays_converted_dollars_and_the_rate_is_kept(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "GH")
    login_client(owner).post("/app/plan/checkout/growth/", {"interval": "month"})
    invoice = Invoice.objects.get()
    assert (invoice.currency, invoice.amount_minor, invoice.fx_ngn_per_usd) == ("USD", 6_297, Decimal("1550.00"))


@override_settings(PAYMENTS_ENABLED=True)
def test_yearly_covers_twelve_months(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "US")
    login_client(owner).post("/app/plan/checkout/growth/", {"interval": "year"})
    attempt = PaymentAttempt.objects.select_related("invoice").get()
    assert attempt.amount_minor == 90_800 and attempt.invoice.interval == "year"
    _flw_pay(attempt, fake_flutterwave, 502)
    sub = Subscription.objects.get(organization=organization)
    assert sub.interval == "year" and (sub.period_end - sub.period_start).days >= 365


@override_settings(PAYMENTS_ENABLED=True)
def test_the_browser_cannot_choose_the_currency_or_price(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "US")
    login_client(owner).post("/app/plan/checkout/growth/", {"interval": "month", "currency": "NGN", "amount": "1"})
    attempt = PaymentAttempt.objects.get()
    assert (attempt.currency, attempt.amount_minor) == ("USD", 8_900)
    with pytest.raises(ValidationFailure):
        billing.start_plan_checkout(organization=organization, membership=owner.memberships.get(),
                                    plan_slug="growth", return_url="https://x.test/", interval="decade")


# ---------- Flutterwave webhook ----------

@override_settings(PAYMENTS_ENABLED=True)
def test_flutterwave_webhook_needs_the_secret_hash(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "GB")
    login_client(owner).post("/app/plan/checkout/starter/", {})
    attempt = PaymentAttempt.objects.get()
    fake_flutterwave.confirm(attempt.reference, attempt.amount_minor)
    response = _flw_webhook({"event": "charge.completed", "data": {"id": 9, "tx_ref": attempt.reference,
                             "amount": 55, "currency": "USD", "status": "successful"}}, signature="wrong")
    assert response.status_code == 401
    assert not Subscription.objects.filter(organization=organization).exists()


@override_settings(PAYMENTS_ENABLED=True)
def test_flutterwave_must_confirm_the_amount(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "GB")
    login_client(owner).post("/app/plan/checkout/starter/", {})
    attempt = PaymentAttempt.objects.get()
    fake_flutterwave.confirm(attempt.reference, 100)  # Flutterwave says only $1 was paid
    _flw_webhook({"event": "charge.completed", "data": {"id": 10, "tx_ref": attempt.reference,
                  "amount": 55, "currency": "USD", "status": "successful"}})
    assert not Subscription.objects.filter(organization=organization).exists()
    assert AuditEvent.objects.filter(action="billing.webhook_not_confirmed").exists()


@override_settings(PAYMENTS_ENABLED=True)
def test_a_paystack_reference_cannot_be_paid_through_flutterwave(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "NG")
    login_client(owner).post("/app/plan/checkout/starter/", {})
    attempt = PaymentAttempt.objects.get()  # a Paystack (naira) attempt
    fake_flutterwave.confirm(attempt.reference, attempt.amount_minor, currency="NGN")
    _flw_webhook({"event": "charge.completed", "data": {"id": 11, "tx_ref": attempt.reference,
                  "amount": attempt.amount_minor / 100, "currency": "NGN", "status": "successful"}})
    assert not Subscription.objects.filter(organization=organization).exists()


@override_settings(PAYMENTS_ENABLED=True)
def test_flutterwave_resends_are_applied_once(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "GB")
    login_client(owner).post("/app/plan/checkout/starter/", {})
    attempt = PaymentAttempt.objects.get()
    _flw_pay(attempt, fake_flutterwave, 600)
    end = Subscription.objects.get(organization=organization).period_end
    assert _flw_pay(attempt, fake_flutterwave, 600).json()["duplicate"] is True
    assert Subscription.objects.get(organization=organization).period_end == end


# ---------- upgrades and the plan page ----------

@override_settings(PAYMENTS_ENABLED=True)
def test_a_dollar_business_upgrades_in_dollars(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "GB")
    login_client(owner).post("/app/plan/checkout/starter/", {})
    _flw_pay(PaymentAttempt.objects.get(), fake_flutterwave, 700)
    response = login_client(owner).post("/app/plan/upgrade/growth/", {})
    assert response["Location"] == fake_flutterwave.checkout_url
    upgrade = PaymentAttempt.objects.select_related("invoice").latest("created_at")
    assert upgrade.currency == "USD" and 0 < upgrade.amount_minor <= 3_400  # at most $89 − $55
    _flw_pay(upgrade, fake_flutterwave, 701)
    assert Subscription.objects.get(organization=organization).plan.slug == "growth"


def test_plan_page_shows_the_business_currency(plans, organization, owner, client):
    _set_country(organization, "GB")
    body = client.get("/app/plan/").content.decode()
    assert "USD 55.00" in body and "USD 561.00" in body and "Flutterwave" in body and "NGN 65,000" not in body
    _set_country(organization, "NG")
    body = client.get("/app/plan/").content.decode()
    assert "NGN 65,000.00" in body and "Paystack" in body and "USD" not in body.split("Services in your plan")[0]


# ---------- country at sign-up and in admin ----------

def test_workspace_setup_asks_and_stores_the_country(db):
    from apps.organizations.models import Organization

    from .conftest import make_user

    user = make_user("newowner@example.test")
    c = login_client(user)
    assert "Where is your business based?" in c.get("/app/onboarding/").content.decode()
    assert c.post("/app/onboarding/", {"name": "Mensah & Co", "country": "ZZ"}).status_code == 200
    c.post("/app/onboarding/", {"name": "Mensah & Co", "country": "GH", "confirm_authority": "on"})
    assert Organization.objects.get(name="Mensah & Co").country == "GH"


def test_only_super_admins_can_change_billing_settings(db):
    from .conftest import make_user

    staff = make_user("ops-admin@example.test", staff_role="admin", mfa=True, is_staff=True)
    assert login_client(staff).get("/admin/billing/billingsettings/").status_code in (302, 403)
    root = make_user("root@example.test", staff_role="admin", mfa=True, is_staff=True)
    root.is_superuser = True
    root.save(update_fields=["is_superuser"])
    assert BillingSettings.current().pk == 1


# ---------- review follow-ups ----------

def test_a_dollars_per_naira_rate_is_turned_the_right_way_up(plans, fake_flutterwave):
    fake_flutterwave.rate = "0.000645"
    assert pricing.ngn_per_usd() == Decimal("1550.39")


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1550", "0", "abc"])
def test_unusable_rates_fall_back_or_stop(plans, fake_flutterwave, bad):
    fake_flutterwave.rate = bad
    with pytest.raises(ValidationFailure, match="exchange rate"):
        pricing.ngn_per_usd()


def test_a_failed_lookup_is_not_repeated_for_every_plan(plans, fake_flutterwave):
    fake_flutterwave.rate = None
    for _ in range(3):
        with pytest.raises(ValidationFailure):
            pricing.ngn_per_usd()
    assert sum(1 for c in fake_flutterwave.calls if c[1].startswith("/transfers/rates")) == 1


@pytest.mark.parametrize("fallback, usable", [("1500", True), ("15.50", False), ("-1500", False)])
def test_the_admin_fallback_rate_must_be_believable(plans, fake_flutterwave, fallback, usable):
    fake_flutterwave.rate = None
    settings = BillingSettings.current()
    BillingSettings.objects.filter(pk=settings.pk).update(fallback_ngn_per_usd=Decimal(fallback))
    if usable:
        assert pricing.ngn_per_usd() == Decimal(fallback)
    else:
        with pytest.raises(ValidationFailure):
            pricing.ngn_per_usd()
    from django.core.exceptions import ValidationError

    settings.refresh_from_db()
    if not usable:
        with pytest.raises(ValidationError):
            settings.full_clean()


@override_settings(PAYMENTS_ENABLED=True)
def test_a_rate_limited_verify_is_retried_not_stored(plans, organization, owner, fake_flutterwave):
    from apps.billing import flutterwave
    from apps.billing.models import ProviderWebhookEvent

    _set_country(organization, "GB")
    login_client(owner).post("/app/plan/checkout/starter/", {})
    attempt = PaymentAttempt.objects.get()
    fake_flutterwave.fail_with = flutterwave.FlutterwaveUnavailable("Flutterwave returned 429.")
    response = _flw_webhook({"event": "charge.completed", "data": {"id": 31, "tx_ref": attempt.reference,
                             "amount": 55, "currency": "USD", "status": "successful"}})
    assert response.status_code == 503 and not ProviderWebhookEvent.objects.exists()
    fake_flutterwave.fail_with = None
    assert _flw_pay(attempt, fake_flutterwave, 31).status_code == 200
    assert Subscription.objects.filter(organization=organization).exists()


def test_http_429_counts_as_temporary(monkeypatch, fake_flutterwave):
    import io
    import urllib.error
    import urllib.request

    from apps.billing import flutterwave

    def raise_429(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.flutterwave.com", 429, "Too Many", {}, io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", raise_429)
    with pytest.raises(flutterwave.FlutterwaveUnavailable):
        fake_flutterwave.original("GET", "/transactions/verify_by_reference?tx_ref=x")


@override_settings(PAYMENTS_ENABLED=True)
def test_a_failed_notice_does_not_block_the_later_success(plans, organization, owner, fake_flutterwave):
    _set_country(organization, "GB")
    login_client(owner).post("/app/plan/checkout/starter/", {})
    attempt = PaymentAttempt.objects.get()
    _flw_webhook({"event": "charge.completed", "data": {"id": 77, "tx_ref": attempt.reference,
                  "amount": 55, "currency": "USD", "status": "failed"}})
    assert not Subscription.objects.filter(organization=organization).exists()
    _flw_pay(attempt, fake_flutterwave, 77)
    assert Subscription.objects.filter(organization=organization).exists()


def test_upgrade_charge_rounds_up_exactly(plans, organization):
    from datetime import timedelta

    from django.utils import timezone

    _set_country(organization, "NG")
    Plan.objects.filter(slug="starter").update(price_minor=1_000)
    Plan.objects.filter(slug="growth").update(price_minor=1_201)  # difference 201 kobo
    now = timezone.now()
    sub = Subscription.objects.create(organization=organization, plan=Plan.objects.get(slug="starter"),
                                      period_start=now - timedelta(days=15), period_end=now + timedelta(days=15))
    # 201 × ½ = 100.5 kobo → rounds up to ₦2, never down to ₦1
    assert billing.upgrade_charge(sub, Plan.objects.get(slug="growth"), now).amount_minor == 200


def test_yearly_plans_get_tasks_month_by_month(plans, organization, owner, make_task):
    from datetime import timedelta

    from django.utils import timezone

    from apps.billing.models import UsageKind, UsageLedger

    _set_country(organization, "GB")
    start = timezone.now() - timedelta(days=40)
    sub = Subscription.objects.create(organization=organization, plan=plans["starter"], interval="year",
                                      period_start=start, period_end=start + timedelta(days=365))
    # All 7 of the first month's tasks were used in the first month.
    for _ in range(7):
        UsageLedger.objects.create(organization=organization, task=make_task(organization, owner),
                                   kind=UsageKind.ALLOWANCE, period_start=sub.period_start)
    assert billing.task_window_start(sub) > sub.period_start
    assert billing.allowance_remaining(organization) == 7  # a fresh month


def test_admin_cannot_save_an_unknown_country(organization):
    from django.core.exceptions import ValidationError

    organization.country = "UK"  # the code is GB
    with pytest.raises(ValidationError):
        organization.full_clean()
    organization.country = ""  # older accounts stay valid
    organization.full_clean()
