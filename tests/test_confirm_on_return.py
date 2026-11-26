"""When the client comes back from Paystack or Flutterwave, the server asks the provider.

The browser's own word (`status=success` in the return address) never counts. The server
asks the provider with its secret key, the same check the webhook path makes, so a paid
plan shows as confirmed in seconds even when the provider's notice is slow or missing
(as happened with the first live-server test payments).
"""
from __future__ import annotations

import io

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings

from apps.billing import paystack
from apps.billing.models import (
    InvoiceStatus,
    PaymentAttempt,
    PaymentStatus,
    Plan,
    ProviderWebhookEvent,
    Subscription,
)

from .conftest import login_client
from .test_plan_checkout import _charge, _webhook

GROWTH_PRICE = 9_750_000


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def growth(db) -> Plan:
    return Plan.objects.create(
        name="Growth", slug="growth", monthly_task_allowance=20, price_minor=GROWTH_PRICE
    )


def _pay_and_return(client, reference_suffix="&status=success"):
    client.post("/app/plan/checkout/growth/", {"renews": "on"})
    attempt = PaymentAttempt.objects.select_related("invoice").get()
    return attempt, f"/app/plan/checkout/return/?reference={attempt.reference}{reference_suffix}"


@pytest.mark.django_db
def test_provider_confirms_on_return_and_the_plan_starts(owner, growth, fake_paystack):
    client = login_client(owner)
    attempt, url = _pay_and_return(client)
    fake_paystack.confirm(attempt.reference, GROWTH_PRICE)

    page = client.get(url)

    assert b"Payment confirmed" in page.content
    assert b'http-equiv="refresh"' not in page.content
    attempt.refresh_from_db()
    attempt.invoice.refresh_from_db()
    assert attempt.status == PaymentStatus.SUCCEEDED and attempt.invoice.status == InvoiceStatus.PAID
    assert Subscription.objects.filter(plan=growth).count() == 1
    event = ProviderWebhookEvent.objects.get(event_id=f"return:{attempt.reference}")
    assert event.outcome == "paid" and event.signature_valid is False


@pytest.mark.django_db
def test_browser_saying_success_is_not_enough(owner, growth, fake_paystack):
    client = login_client(owner)
    attempt, url = _pay_and_return(client)
    fake_paystack.confirm(attempt.reference, GROWTH_PRICE, status="abandoned")

    page = client.get(url)

    assert b"Confirming your payment" in page.content
    assert not Subscription.objects.exists()
    assert not ProviderWebhookEvent.objects.exists()  # nothing stored, so a later check can still apply it


@pytest.mark.django_db
def test_provider_amount_must_match(owner, growth, fake_paystack):
    client = login_client(owner)
    attempt, url = _pay_and_return(client)
    fake_paystack.confirm(attempt.reference, 100)  # only ₦1 actually paid

    client.get(url)

    assert not Subscription.objects.exists()
    attempt.invoice.refresh_from_db()
    assert attempt.invoice.status == InvoiceStatus.ISSUED


@pytest.mark.django_db
def test_later_webhook_does_not_apply_twice(owner, growth, fake_paystack):
    client = login_client(owner)
    attempt, url = _pay_and_return(client)
    fake_paystack.confirm(attempt.reference, GROWTH_PRICE)
    client.get(url)

    response = _webhook(client, _charge(attempt.reference, GROWTH_PRICE, event_id=4242))

    assert response.status_code == 200
    assert Subscription.objects.filter(plan=growth).count() == 1
    assert ProviderWebhookEvent.objects.get(event_id="4242").outcome == "already_applied"


@pytest.mark.django_db
def test_reloads_ask_the_provider_at_most_every_few_seconds(owner, growth, fake_paystack):
    client = login_client(owner)
    _attempt, url = _pay_and_return(client)
    verify_calls = lambda: sum(1 for _, path, _ in fake_paystack.calls if path.startswith("/transaction/verify/"))

    client.get(url)
    client.get(url)
    client.get(url)

    assert verify_calls() == 1


@pytest.mark.django_db
def test_provider_down_just_keeps_waiting(owner, growth, fake_paystack):
    client = login_client(owner)
    _attempt, url = _pay_and_return(client)
    fake_paystack.fail_with = paystack.PaystackUnavailable("down")

    page = client.get(url)

    assert page.status_code == 200 and b"Confirming your payment" in page.content
    assert not Subscription.objects.exists()


@pytest.mark.django_db
def test_nothing_is_asked_once_paid(owner, growth, fake_paystack):
    client = login_client(owner)
    attempt, url = _pay_and_return(client)
    fake_paystack.confirm(attempt.reference, GROWTH_PRICE)
    client.get(url)
    cache.clear()
    calls_before = len(fake_paystack.calls)

    client.get(url)

    assert len(fake_paystack.calls) == calls_before


@pytest.mark.django_db
def test_another_business_cannot_trigger_the_check(owner, other_owner, growth, fake_paystack):
    attempt, url = _pay_and_return(login_client(owner))
    fake_paystack.confirm(attempt.reference, GROWTH_PRICE)

    assert login_client(other_owner).get(url).status_code == 404
    assert not Subscription.objects.exists()


@pytest.mark.django_db
@override_settings(PAYMENTS_ENABLED=True)
def test_flutterwave_confirms_on_return(organization, owner, fake_flutterwave):
    call_command("seed_plans", stdout=io.StringIO())
    organization.country = "GB"
    organization.save(update_fields=["country"])
    client = login_client(owner)
    client.post("/app/plan/checkout/starter/", {})
    attempt = PaymentAttempt.objects.get()
    assert attempt.provider == "flutterwave"
    fake_flutterwave.confirm(attempt.reference, attempt.amount_minor, currency=attempt.currency)

    page = client.get(f"/app/plan/checkout/return/?status=successful&tx_ref={attempt.reference}&transaction_id=1")

    assert b"Payment confirmed" in page.content
    assert Subscription.objects.filter(organization=organization, plan__slug="starter").count() == 1
