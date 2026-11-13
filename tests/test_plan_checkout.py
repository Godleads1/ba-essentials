"""Plan checkout with Paystack: the server sets the price, Paystack confirms the payment,
and only a confirmed payment starts a plan (guide section 4.5; audit F-02, F-05)."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from django.conf import settings
from django.test import Client

from apps.audit.models import AuditEvent
from apps.billing import paystack
from apps.billing import services as billing
from apps.billing.models import (
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
    PaymentStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
)

from .conftest import login_client

GROWTH_PRICE = 9_750_000  # ₦97,500 in kobo


@pytest.fixture
def growth(db) -> Plan:
    return Plan.objects.create(
        name="Growth", slug="growth", monthly_task_allowance=20, price_minor=GROWTH_PRICE
    )


def _start(owner_client, slug="growth", **extra):
    return owner_client.post(f"/app/plan/checkout/{slug}/", extra)


def _webhook(client, payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(settings.PAYSTACK_WEBHOOK_SECRET.encode(), raw, hashlib.sha512).hexdigest()
    return client.post(
        "/webhooks/paystack/", raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature
    )


def _charge(reference, amount, event_id, currency="NGN"):
    return {
        "event": "charge.success",
        "data": {"id": event_id, "reference": reference, "amount": amount, "currency": currency, "status": "success"},
    }


def _only_attempt():
    return PaymentAttempt.objects.select_related("invoice").get()


# ---------- starting checkout ----------

@pytest.mark.django_db
def test_checkout_uses_the_server_price_and_sends_owner_to_paystack(owner, growth, fake_paystack):
    response = _start(login_client(owner), amount="100", amount_minor="100", currency="USD", plan="free")
    assert response.status_code == 302
    assert response["Location"] == fake_paystack.checkout_url

    attempt = _only_attempt()
    assert attempt.amount_minor == GROWTH_PRICE and attempt.currency == "NGN"
    assert attempt.invoice.plan == growth and attempt.invoice.status == InvoiceStatus.ISSUED
    method, path, payload = fake_paystack.calls[0]
    assert (method, path) == ("POST", "/transaction/initialize")
    assert payload["amount"] == GROWTH_PRICE and payload["email"] == owner.email
    assert payload["reference"] == attempt.reference
    assert payload["callback_url"].endswith("/app/plan/checkout/return/")
    # Starting checkout activates nothing.
    assert not Subscription.objects.exists()


@pytest.mark.django_db
def test_only_the_owner_can_start_checkout(member, growth, fake_paystack):
    response = _start(login_client(member))
    assert response["Location"] == "/app/plan/"
    assert not Invoice.objects.exists() and not fake_paystack.calls


@pytest.mark.django_db
def test_free_inactive_or_unknown_plans_cannot_be_bought(owner, plan, growth):
    client = login_client(owner)
    growth.is_active = False
    growth.save()
    for slug in ("growth", "starter", "no-such-plan"):  # inactive, price 0, missing
        assert _start(client, slug)["Location"] == "/app/plan/"
    assert not Invoice.objects.exists()


@pytest.mark.django_db
def test_no_second_plan_while_one_is_active(owner, growth, subscription):
    assert _start(login_client(owner))["Location"] == "/app/plan/"
    assert not Invoice.objects.exists()


@pytest.mark.django_db
def test_anonymous_and_staff_cannot_start_checkout(growth, ops_user):
    assert Client().post("/app/plan/checkout/growth/")["Location"].startswith("/login")
    staff = login_client(ops_user).post("/app/plan/checkout/growth/")
    assert staff.status_code == 302 and "/staff/" in staff["Location"]
    assert not Invoice.objects.exists()


@pytest.mark.django_db
def test_checkout_must_be_a_post(owner, growth):
    assert login_client(owner).get("/app/plan/checkout/growth/").status_code == 405


@pytest.mark.django_db
def test_unexpected_checkout_address_is_refused(owner, growth, fake_paystack):
    fake_paystack.checkout_url = "https://evil.example/pay"
    response = _start(login_client(owner))
    assert response["Location"] == "/app/plan/"
    attempt = _only_attempt()
    assert attempt.status == PaymentStatus.FAILED and attempt.invoice.status == InvoiceStatus.VOID


@pytest.mark.django_db
def test_paystack_down_at_checkout_voids_the_invoice(owner, growth, fake_paystack):
    fake_paystack.fail_with = paystack.PaystackUnavailable("down")
    assert _start(login_client(owner))["Location"] == "/app/plan/"
    assert _only_attempt().invoice.status == InvoiceStatus.VOID


# ---------- the return page ----------

@pytest.mark.django_db
def test_return_page_never_activates_a_plan(owner, growth):
    client = login_client(owner)
    _start(client)
    attempt = _only_attempt()
    page = client.get(f"/app/plan/checkout/return/?reference={attempt.reference}&trxref={attempt.reference}&status=success")
    assert page.status_code == 200
    assert b"Confirming your payment" in page.content
    assert not Subscription.objects.exists()
    attempt.invoice.refresh_from_db()
    assert attempt.invoice.status == InvoiceStatus.ISSUED


@pytest.mark.django_db
def test_return_page_is_tenant_scoped(owner, other_owner, growth):
    _start(login_client(owner))
    reference = _only_attempt().reference
    assert login_client(other_owner).get(f"/app/plan/checkout/return/?reference={reference}").status_code == 404


# ---------- the webhook and Paystack's confirmation ----------

@pytest.mark.django_db
def test_confirmed_payment_starts_the_plan_once(owner, growth, fake_paystack):
    _start(login_client(owner))
    attempt = _only_attempt()
    fake_paystack.confirm(attempt.reference, GROWTH_PRICE)

    response = _webhook(Client(), _charge(attempt.reference, GROWTH_PRICE, 7001))
    assert response.status_code == 200 and response.json()["outcome"] == "paid"
    subscription = Subscription.objects.get()
    assert subscription.status == SubscriptionStatus.ACTIVE and subscription.plan == growth
    period_end = subscription.period_end

    # The same event again, and a different event for the same payment, change nothing.
    assert _webhook(Client(), _charge(attempt.reference, GROWTH_PRICE, 7001)).json()["duplicate"] is True
    assert _webhook(Client(), _charge(attempt.reference, GROWTH_PRICE, 7002)).status_code == 200
    subscription.refresh_from_db()
    assert subscription.period_end == period_end
    assert AuditEvent.objects.filter(action="billing.subscription_activated").count() == 1

    page = login_client(owner).get(f"/app/plan/checkout/return/?reference={attempt.reference}")
    assert b"Payment confirmed" in page.content


@pytest.mark.django_db
@pytest.mark.parametrize(
    "paystack_says",
    [
        {"status": "failed"},
        {"status": "abandoned"},
        {"amount": GROWTH_PRICE - 100},
        {"currency": "USD"},
        {"reference": "BA-SOMEONE-ELSE"},
        None,  # Paystack doesn't know this transaction
    ],
)
def test_webhook_alone_never_starts_a_plan(owner, growth, fake_paystack, paystack_says):
    _start(login_client(owner))
    attempt = _only_attempt()
    if paystack_says is not None:
        fake_paystack.confirm(attempt.reference, GROWTH_PRICE)
        fake_paystack.verified[attempt.reference].update(paystack_says)

    response = _webhook(Client(), _charge(attempt.reference, GROWTH_PRICE, 7100))
    assert response.json()["outcome"] == "not_confirmed"
    assert not Subscription.objects.exists()
    attempt.invoice.refresh_from_db()
    assert attempt.invoice.status == InvoiceStatus.ISSUED
    assert AuditEvent.objects.filter(action="billing.webhook_not_confirmed", result="denied").exists()


@pytest.mark.django_db
def test_paystack_unreachable_asks_for_a_retry_and_keeps_nothing(owner, growth, fake_paystack):
    _start(login_client(owner))
    attempt = _only_attempt()
    fake_paystack.fail_with = paystack.PaystackUnavailable("timeout")
    response = _webhook(Client(), _charge(attempt.reference, GROWTH_PRICE, 7200))
    assert response.status_code == 503
    assert not Subscription.objects.exists()

    # Paystack retries the same event once it's reachable again, and it goes through.
    fake_paystack.fail_with = None
    fake_paystack.confirm(attempt.reference, GROWTH_PRICE)
    retry = _webhook(Client(), _charge(attempt.reference, GROWTH_PRICE, 7200))
    assert retry.json() == {"status": "ok", "duplicate": False, "outcome": "paid"}
    assert Subscription.objects.get().status == SubscriptionStatus.ACTIVE


@pytest.mark.django_db
def test_forged_webhook_is_rejected_before_asking_paystack(owner, growth, fake_paystack):
    _start(login_client(owner))
    attempt = _only_attempt()
    calls_before = len(fake_paystack.calls)
    raw = json.dumps(_charge(attempt.reference, GROWTH_PRICE, 7300)).encode()
    response = Client().post("/webhooks/paystack/", raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE="0" * 128)
    assert response.status_code == 401
    assert len(fake_paystack.calls) == calls_before
    assert not Subscription.objects.exists()


@pytest.mark.django_db
def test_paying_early_extends_instead_of_losing_days(owner, organization, growth, fake_paystack):
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    current = Subscription.objects.create(
        organization=organization, plan=growth, period_start=now, period_end=billing.add_months(now, 1),
        status=SubscriptionStatus.ACTIVE,
    )
    invoice = Invoice.objects.create(number="INV-T-1", organization=organization, plan=growth,
                                     amount_minor=GROWTH_PRICE, currency="NGN")
    billing._activate_subscription(invoice)
    current.refresh_from_db()
    assert current.period_start == datetime(2026, 12, 13, 12, 0, tzinfo=UTC)
    assert current.period_end == datetime(2027, 1, 13, 12, 0, tzinfo=UTC)


def test_month_arithmetic_clamps_to_shorter_months():
    jan31 = datetime(2026, 1, 31, 9, 30, tzinfo=UTC)
    assert billing.add_months(jan31, 1) == datetime(2026, 2, 28, 9, 30, tzinfo=UTC)
    assert billing.add_months(datetime(2027, 12, 15, tzinfo=UTC), 1).year == 2028


@pytest.mark.django_db
def test_trying_again_reuses_the_open_invoice_and_both_references_pay_it(owner, growth, fake_paystack):
    client = login_client(owner)
    _start(client)
    _start(client)
    attempts = list(PaymentAttempt.objects.order_by("created_at"))
    assert len(attempts) == 2 and Invoice.objects.count() == 1
    assert attempts[0].reference != attempts[1].reference

    # The first, abandoned-looking checkout is paid late: it still pays the one invoice.
    fake_paystack.confirm(attempts[0].reference, GROWTH_PRICE)
    assert _webhook(Client(), _charge(attempts[0].reference, GROWTH_PRICE, 7400)).json()["outcome"] == "paid"
    assert Subscription.objects.get().status == SubscriptionStatus.ACTIVE
