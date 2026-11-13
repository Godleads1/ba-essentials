"""Each plan unlocks only its own services, legal reviews come from their own allowance,
and moving up mid-cycle charges the difference and changes the plan only once Paystack
confirms it."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client, override_settings
from django.utils import timezone

from apps.billing import plan_rules
from apps.billing import services as billing
from apps.billing.models import (
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
    Plan,
    Subscription,
    SubscriptionStatus,
    UsageKind,
    UsageLedger,
)
from apps.catalogue.models import ServiceCategory, ServiceDefinition, ServiceState
from apps.core.exceptions import PermissionFailure, ValidationFailure

from .conftest import login_client


@pytest.fixture
def plans(db):
    call_command("seed_plans", stdout=open("/dev/null", "w"))
    return {p.slug: p for p in Plan.objects.all()}


def _subscribe(organization, plan, *, started_days_ago=1, period_days=30):
    now = timezone.now()
    sub = Subscription.objects.create(
        organization=organization, plan=plan,
        period_start=now - timedelta(days=started_days_ago),
        period_end=now - timedelta(days=started_days_ago) + timedelta(days=period_days),
        status=SubscriptionStatus.ACTIVE,
    )
    Subscription.objects.filter(pk=sub.pk).update(created_at=now - timedelta(days=started_days_ago))
    sub.refresh_from_db()
    return sub


def _service(slug, name, *, minimum=0, legal=False):
    category = ServiceCategory.objects.create(
        name=name, slug=slug, minimum_plan_rank=minimum, uses_legal_allowance=legal
    )
    definition = ServiceDefinition.objects.create(category=category, name=f"{name} service", slug=f"{slug}-svc")
    return definition.versions.create(
        version_number=1, scope_summary="Synthetic scope for tests.", deliverables_summary="A file.",
        state=ServiceState.AVAILABLE, is_current=True,
    )


@pytest.fixture
def services(db):
    return {
        "business": _service("business-admin", "Business & Admin"),
        "legal": _service("legal-ip", "Legal & IP Review", legal=True),
        "ops": _service("operations", "Operations & Logistics", minimum=2),
        "addons": _service("add-ons", "Specialized Add-Ons", minimum=3),
    }


# ---------- plans as published ----------

def test_seeded_plans_match_the_pricing_page(plans):
    starter, growth, dedicated = plans["starter"], plans["growth"], plans["dedicated"]
    assert (starter.price_minor, starter.monthly_task_allowance, starter.legal_review_allowance,
            starter.legal_review_period_months) == (6_500_000, 7, 1, 3)
    assert (growth.price_minor, growth.monthly_task_allowance, growth.legal_review_allowance,
            growth.legal_review_period_months) == (9_750_000, 20, 1, 1)
    assert (dedicated.price_minor, dedicated.monthly_task_allowance, dedicated.legal_review_allowance) == (26_000_000, 30, 4)
    assert starter.rank < growth.rank < dedicated.rank


# ---------- which services each plan can request ----------

@pytest.mark.parametrize("plan_slug, allowed", [
    ("starter", {"business", "legal"}),
    ("growth", {"business", "legal", "ops"}),
    ("dedicated", {"business", "legal", "ops", "addons"}),
])
def test_each_plan_can_request_only_its_own_categories(plan_slug, allowed, plans, services, organization, owner, make_task):
    _subscribe(organization, plans[plan_slug])
    for key, version in services.items():
        if key in allowed:
            make_task(organization, owner, service_version=version)
        else:
            with pytest.raises(ValidationFailure, match="Upgrade your plan"):
                make_task(organization, owner, service_version=version)


def test_a_business_without_a_plan_can_only_request_open_categories(plans, services, organization, owner, make_task):
    make_task(organization, owner, service_version=services["business"])
    with pytest.raises(ValidationFailure, match="from the Growth plan"):
        make_task(organization, owner, service_version=services["ops"])
    Plan.objects.all().delete()
    with pytest.raises(ValidationFailure, match="on a bigger plan"):
        make_task(organization, owner, service_version=services["ops"])


def test_posting_a_locked_service_directly_is_refused(plans, services, organization, owner, client):
    _subscribe(organization, plans["starter"])
    response = client.post("/app/tasks/new/", {
        "service_version": str(services["ops"].pk), "title": "Vendor sourcing",
        "brief": "Find three packaging vendors in Lagos for us.", "priority": "medium",
    })
    from apps.tasks.models import Task

    assert not Task.objects.filter(service_version=services["ops"]).exists()
    assert response.status_code in (200, 302)


def test_the_form_and_catalogue_show_locked_categories(plans, services, organization, owner, client):
    _subscribe(organization, plans["starter"])
    form_page = client.get("/app/tasks/new/").content.decode()
    assert "Operations &amp; Logistics service" not in form_page.split("Not in your plan")[0]
    assert "Not in your plan" in form_page and "from Growth" in form_page
    catalogue = client.get("/app/catalogue/").content.decode()
    assert "On Growth" in catalogue and "Upgrade to Growth" in catalogue and "On Dedicated" in catalogue


def test_cancelling_and_downgrading_never_unlocks_bigger_categories(plans, services, organization, owner, make_task):
    sub = _subscribe(organization, plans["growth"])
    make_task(organization, owner, service_version=services["ops"])
    Subscription.objects.filter(pk=sub.pk).update(status=SubscriptionStatus.CANCELLED)
    with pytest.raises(ValidationFailure):
        make_task(organization, owner, service_version=services["ops"])


# ---------- legal review allowance ----------

def _task_in(category_version, organization, owner, make_task):
    return make_task(organization, owner, service_version=category_version)


def test_starter_gets_one_legal_review_a_quarter_without_using_tasks(plans, services, organization, owner, make_task):
    _subscribe(organization, plans["starter"], started_days_ago=40, period_days=60)
    first = _task_in(services["legal"], organization, owner, make_task)
    second = _task_in(services["legal"], organization, owner, make_task)
    assert billing.reserve_allowance(first) is True
    assert UsageLedger.objects.get(task=first).kind == UsageKind.LEGAL
    assert billing.allowance_remaining(organization) == 7  # tasks untouched
    assert plan_rules.legal_reviews_remaining(organization) == 0
    assert billing.reserve_allowance(second) is False  # quoted instead


def _at(y, m, d):
    from datetime import UTC, datetime

    return datetime(y, m, d, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize("started, now, expected", [
    (_at(2026, 1, 31), _at(2026, 4, 29), _at(2026, 1, 31)),   # still in the first quarter
    (_at(2026, 1, 31), _at(2026, 4, 30), _at(2026, 4, 30)),   # Jan 31 + 3 months = Apr 30
    (_at(2026, 11, 13), _at(2027, 5, 12), _at(2027, 2, 13)),
    (_at(2026, 11, 13), _at(2027, 5, 13), _at(2027, 5, 13)),
])
def test_starter_legal_window_is_each_quarter_from_the_start_date(plans, organization, started, now, expected):
    sub = _subscribe(organization, plans["starter"])
    Subscription.objects.filter(pk=sub.pk).update(created_at=started)
    sub.refresh_from_db()
    assert plan_rules.legal_window_start(sub, now) == expected


@pytest.mark.parametrize("started, now, expected", [
    (_at(2027, 2, 24), _at(2027, 4, 5), _at(2027, 3, 24)),
    (_at(2026, 1, 31), _at(2026, 3, 1), _at(2026, 2, 28)),
])
def test_growth_legal_window_is_each_month_from_the_start_date(plans, organization, started, now, expected):
    sub = _subscribe(organization, plans["growth"])
    Subscription.objects.filter(pk=sub.pk).update(created_at=started)
    sub.refresh_from_db()
    assert plan_rules.legal_window_start(sub, now) == expected


def test_moving_from_starter_to_growth_does_not_give_an_extra_review(plans, services, organization, owner, make_task):
    sub = _subscribe(organization, plans["starter"], started_days_ago=50, period_days=60)
    first = _task_in(services["legal"], organization, owner, make_task)
    assert billing.reserve_allowance(first) is True  # Starter's review this quarter, used today
    Subscription.objects.filter(pk=sub.pk).update(plan=plans["growth"])
    # Growth allows 1 a month; the one used today still counts this month.
    assert plan_rules.legal_reviews_remaining(organization) == 0
    second = _task_in(services["legal"], organization, owner, make_task)
    assert billing.reserve_allowance(second) is False


def test_dedicated_gets_four_legal_reviews(plans, services, organization, owner, make_task):
    _subscribe(organization, plans["dedicated"])
    tasks = [_task_in(services["legal"], organization, owner, make_task) for _ in range(5)]
    assert [billing.reserve_allowance(t) for t in tasks] == [True, True, True, True, False]


def test_other_work_uses_the_task_allowance(plans, services, organization, owner, make_task):
    _subscribe(organization, plans["starter"])
    task = _task_in(services["business"], organization, owner, make_task)
    assert billing.reserve_allowance(task) is True
    assert UsageLedger.objects.get(task=task).kind == UsageKind.ALLOWANCE
    assert plan_rules.legal_reviews_remaining(organization) == 1
    assert billing.eligibility(task).allowed


def test_cancelling_a_legal_request_gives_the_review_back(plans, services, organization, owner, make_task):
    _subscribe(organization, plans["starter"])
    task = _task_in(services["legal"], organization, owner, make_task)
    billing.reserve_allowance(task)
    billing.release_allowance(task)
    assert plan_rules.legal_reviews_remaining(organization) == 1


# ---------- moving up mid-cycle ----------

def _webhook(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(settings.PAYSTACK_WEBHOOK_SECRET.encode(), raw, hashlib.sha512).hexdigest()
    return Client().post("/webhooks/paystack/", raw, content_type="application/json",
                         HTTP_X_PAYSTACK_SIGNATURE=signature)


def _pay(attempt, fake_paystack, event_id):
    fake_paystack.confirm(attempt.reference, attempt.amount_minor)
    _webhook({"event": "charge.success", "data": {"id": event_id, "reference": attempt.reference,
              "amount": attempt.amount_minor, "currency": "NGN", "status": "success"}})
    attempt.refresh_from_db()
    return attempt


def test_upgrade_charge_is_the_difference_for_the_days_left(plans, organization):
    sub = _subscribe(organization, plans["starter"], started_days_ago=15, period_days=30)
    charge = billing.upgrade_charge_minor(sub, plans["growth"])
    assert 1_620_000 <= charge <= 1_630_000  # ₦32,500 × 15/30 = ₦16,250
    assert charge % 100 == 0


@override_settings(PAYMENTS_ENABLED=True)
def test_upgrade_unlocks_growth_only_after_paystack_confirms(plans, services, organization, owner, make_task, fake_paystack):
    sub = _subscribe(organization, plans["starter"], started_days_ago=10)
    period_end = sub.period_end
    response = login_client(owner).post("/app/plan/upgrade/growth/")
    assert response["Location"].startswith("https://checkout.paystack.com/")
    attempt = PaymentAttempt.objects.select_related("invoice").get()
    assert attempt.invoice.upgrade_from_id == plans["starter"].pk
    # Not paid yet: still Starter.
    with pytest.raises(ValidationFailure):
        make_task(organization, owner, service_version=services["ops"])
    assert _pay(attempt, fake_paystack, 8101).status == "succeeded"
    sub.refresh_from_db()
    assert sub.plan_id == plans["growth"].pk and sub.period_end == period_end
    make_task(organization, owner, service_version=services["ops"])


@override_settings(PAYMENTS_ENABLED=True)
def test_only_the_owner_can_upgrade_and_only_upwards(plans, organization, owner, member):
    _subscribe(organization, plans["growth"])
    with pytest.raises(PermissionFailure):
        billing.start_upgrade_checkout(organization=organization, membership=member.memberships.get(),
                                       plan_slug="dedicated", return_url="https://x.test/")
    for slug in ("starter", "growth"):
        with pytest.raises(ValidationFailure):
            billing.start_upgrade_checkout(organization=organization, membership=owner.memberships.get(),
                                           plan_slug=slug, return_url="https://x.test/")


@override_settings(PAYMENTS_ENABLED=True)
def test_a_late_payment_for_a_replaced_upgrade_is_refund_due(plans, organization, owner, fake_paystack):
    _subscribe(organization, plans["starter"])
    c = login_client(owner)
    c.post("/app/plan/upgrade/growth/")
    c.post("/app/plan/upgrade/dedicated/")
    old, new = PaymentAttempt.objects.order_by("created_at")
    assert Invoice.objects.get(pk=old.invoice_id).status == InvoiceStatus.VOID
    assert _pay(old, fake_paystack, 8201).status == "refund_due"
    assert Subscription.objects.get(organization=organization).plan_id == plans["starter"].pk
    assert _pay(new, fake_paystack, 8202).status == "succeeded"
    assert Subscription.objects.get(organization=organization).plan_id == plans["dedicated"].pk


@override_settings(PAYMENTS_ENABLED=True)
def test_an_upgrade_priced_for_last_period_is_refund_due_after_renewal(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"], started_days_ago=29, period_days=30)
    login_client(owner).post("/app/plan/upgrade/growth/")
    attempt = PaymentAttempt.objects.get()
    # The period ends and Starter renews before the old, cheap upgrade is paid.
    Subscription.objects.filter(pk=sub.pk).update(
        period_start=sub.period_end, period_end=sub.period_end + timedelta(days=30)
    )
    assert _pay(attempt, fake_paystack, 8401).status == "refund_due"
    assert Subscription.objects.get(pk=sub.pk).plan_id == plans["starter"].pk


@override_settings(PAYMENTS_ENABLED=True)
def test_an_upgrade_paid_after_the_plan_changed_is_refund_due(plans, organization, owner, fake_paystack):
    sub = _subscribe(organization, plans["starter"])
    login_client(owner).post("/app/plan/upgrade/growth/")
    attempt = PaymentAttempt.objects.get()
    Subscription.objects.filter(pk=sub.pk).update(plan=plans["dedicated"])
    assert _pay(attempt, fake_paystack, 8301).status == "refund_due"
    assert Invoice.objects.get(pk=attempt.invoice_id).status == InvoiceStatus.VOID
    assert Subscription.objects.get(pk=sub.pk).plan_id == plans["dedicated"].pk


def test_plan_page_shows_legal_allowance_services_and_upgrades(plans, services, organization, owner, client):
    _subscribe(organization, plans["starter"])
    body = client.get("/app/plan/").content.decode()
    assert "Legal reviews remaining" in body and "1 legal review a quarter" in body
    assert "Services in your plan" in body and "On Growth" in body
    assert "Move up" in body and "Growth" in body and "Dedicated" in body


# ---------- catalogue manifest ----------

def _manifest(tmp_path, category):
    path = tmp_path / "catalogue.json"
    category.setdefault("services", [])
    path.write_text(json.dumps({"categories": [category]}))
    return str(path)


def test_an_older_manifest_keeps_existing_plan_access(db, tmp_path):
    call_command("bootstrap_catalogue", _manifest(tmp_path, {
        "name": "Operations & Logistics", "slug": "operations", "minimum_plan_rank": 2}), stdout=open("/dev/null", "w"))
    call_command("bootstrap_catalogue", _manifest(tmp_path, {
        "name": "Operations & Logistics", "slug": "operations"}), stdout=open("/dev/null", "w"))
    assert ServiceCategory.objects.get(slug="operations").minimum_plan_rank == 2


def test_the_manifest_rejects_a_bad_plan_rank(db, tmp_path):
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("bootstrap_catalogue", _manifest(tmp_path, {
            "name": "Ops", "slug": "ops", "minimum_plan_rank": "growth"}), stdout=open("/dev/null", "w"))
