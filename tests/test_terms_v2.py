"""The platform does what Terms of Service v2 says: held grace-period requests (5.6.3), yearly
prices (5.1), confirming important changes before paying (10.1(b)) and the published page."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.billing import renewals
from apps.billing import services as billing
from apps.billing.models import Invoice, SubscriptionStatus, UsageLedger
from apps.tasks.models import TaskStatus
from apps.tasks.services import cancel_by_client
from apps.tasks.transitions import Action, HeldForRenewal, transition

from .test_renewals import _pay, _subscribe


@pytest.fixture
def plans(db, organization):
    from django.core.management import call_command

    from apps.billing.models import Plan

    call_command("seed_plans", stdout=open("/dev/null", "w"))  # noqa: SIM115
    return {p.slug: p for p in Plan.objects.all()}


def _in_grace(organization, plan):
    sub = _subscribe(organization, plan, ends_in=-timedelta(hours=1))
    renewals.run(timezone.now())  # the period has ended unpaid: grace starts, the renewal invoice is issued
    sub.refresh_from_db()
    assert sub.grace_until and sub.grace_until > timezone.now()
    return sub


# ---------- 5.6.3: requests sent in the grace period are held ----------

def test_requests_before_the_period_ends_are_not_held(plans, organization, owner, make_task):
    _subscribe(organization, plans["starter"])
    assert make_task(organization, owner).held_for_renewal is False


def test_a_grace_period_request_is_held_and_staff_cannot_move_it(plans, organization, owner, make_task, ops_user):
    _in_grace(organization, plans["starter"])
    task = make_task(organization, owner)
    assert task.held_for_renewal and task.status == TaskStatus.SUBMITTED
    with pytest.raises(HeldForRenewal):
        transition(task=task, action=Action.TRIAGE, actor=ops_user)
    task.refresh_from_db()
    assert task.status == TaskStatus.SUBMITTED


def test_the_client_can_still_cancel_a_held_request(plans, organization, owner, make_task):
    _in_grace(organization, plans["starter"])
    task = make_task(organization, owner)
    cancel_by_client(task, owner, "Not needed any more")
    task.refresh_from_db()
    assert task.status == TaskStatus.CANCELLED and not UsageLedger.objects.filter(task=task).exists()


def test_paying_the_renewal_releases_held_requests(plans, organization, owner, make_task, ops_user, fake_paystack):
    sub = _in_grace(organization, plans["starter"])
    task = make_task(organization, owner)
    invoice = Invoice.objects.get(renews_period_end=sub.period_end)
    _pay(billing._create_attempt(invoice, owner, {}), fake_paystack, 9901)
    task.refresh_from_db()
    assert task.held_for_renewal is False
    transition(task=task, action=Action.TRIAGE, actor=ops_user)  # staff can start it now
    task.refresh_from_db()
    assert task.status == TaskStatus.TRIAGE


def test_held_requests_are_cancelled_and_refunded_when_the_plan_ends_unpaid(plans, organization, owner, make_task):
    sub = _in_grace(organization, plans["starter"])
    task = make_task(organization, owner)
    paused = sub.grace_until + timedelta(minutes=1)
    renewals.run(paused)
    sub.refresh_from_db()
    assert sub.status == SubscriptionStatus.PAST_DUE
    renewals.run(sub.paused_at + timedelta(days=30, minutes=1))
    task.refresh_from_db()
    assert task.status == TaskStatus.CANCELLED and not UsageLedger.objects.filter(task=task).exists()


def test_both_sides_are_told_a_request_is_held(plans, organization, owner, make_task, ops_user):
    from .conftest import login_client

    _in_grace(organization, plans["starter"])
    task = make_task(organization, owner)
    assert "Waiting for your renewal payment" in login_client(owner).get(f"/app/tasks/{task.pk}/").content.decode()
    staff = login_client(ops_user)
    assert "Held until the client pays their renewal" in staff.get(f"/staff/tasks/{task.pk}/").content.decode()


# ---------- 10.1(b): confirm important changes before paying for a new period ----------

def _old_terms_client(make_user_fn):
    from apps.accounts.models import ConsentRecord

    user = make_user_fn("old.terms@example.test")
    ConsentRecord.objects.filter(user=user).update(version="Terms 0.2 (17 Nov 2026); Privacy 0.1 (12 Nov 2026)")
    return user


@pytest.fixture
def old_terms_owner(db, organization):
    from apps.organizations.models import Membership, MembershipRole

    from .conftest import make_user

    user = _old_terms_client(make_user)
    Membership.objects.create(organization=organization, user=user, role=MembershipRole.OWNER)
    return user


def test_clients_on_older_terms_are_asked_to_confirm(old_terms_owner, owner):
    from apps.accounts.profile import needs_terms_confirmation

    from .conftest import login_client

    assert needs_terms_confirmation(old_terms_owner) and not needs_terms_confirmation(owner)
    body = login_client(old_terms_owner).get("/app/plan/").content.decode()
    assert "We've updated our Terms of Service." in body and "/app/terms-update/" in body
    assert "We've updated our Terms of Service." not in login_client(owner).get("/app/plan/").content.decode()


def test_staff_are_never_asked(ops_user):
    from apps.accounts.profile import needs_terms_confirmation

    assert not needs_terms_confirmation(ops_user)


def test_paying_for_a_new_period_waits_for_confirmation(plans, organization, old_terms_owner, settings):
    from apps.core.exceptions import ValidationFailure

    settings.PAYMENTS_ENABLED = True
    membership = old_terms_owner.memberships.get()
    with pytest.raises(ValidationFailure, match="updated our Terms"):
        billing.start_plan_checkout(organization=organization, membership=membership, plan_slug="starter",
                                    return_url="http://testserver/app/plan/")
    sub = _in_grace(organization, plans["starter"])
    invoice = Invoice.objects.get(renews_period_end=sub.period_end)
    with pytest.raises(ValidationFailure, match="updated our Terms"):
        billing.start_invoice_checkout(invoice=invoice, membership=membership, return_url="http://testserver/")


def test_confirming_records_the_new_version_and_unblocks(old_terms_owner):
    from apps.accounts.models import ConsentKind, ConsentRecord
    from apps.accounts.profile import needs_terms_confirmation
    from apps.core.legal import AGREEMENT_VERSION

    from .conftest import login_client

    c = login_client(old_terms_owner)
    page = c.get("/app/terms-update/").content.decode()
    assert "What's changed" in page and "14 days" in page
    c.post("/app/terms-update/", {})  # not ticked: nothing recorded
    assert needs_terms_confirmation(old_terms_owner)
    response = c.post("/app/terms-update/", {"agree": "yes"})
    assert response.status_code == 302 and not needs_terms_confirmation(old_terms_owner)
    record = ConsentRecord.objects.filter(user=old_terms_owner, kind=ConsentKind.AGREEMENT).latest("created_at")
    assert record.version == AGREEMENT_VERSION and record.channel == "terms-update"


def test_the_confirmation_page_works_while_paused(plans, organization, old_terms_owner):
    from .conftest import login_client

    sub = _in_grace(organization, plans["starter"])
    renewals.run(sub.grace_until + timedelta(minutes=1))
    c = login_client(old_terms_owner)
    assert c.get("/app/terms-update/").status_code == 200
    assert c.post("/app/terms-update/", {"agree": "yes"}).status_code == 302


# ---------- the published Terms and the platform say the same thing ----------

def _terms_page():
    import html
    import re

    from django.test import Client

    body = Client().get("/terms/").content.decode()
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))


def test_the_terms_page_is_version_2_with_the_right_address(db):
    from apps.core.legal import TERMS_EFFECTIVE_DATE

    text = _terms_page()
    assert f"Version 2 · Effective date: {TERMS_EFFECTIVE_DATE}" in text
    assert text.count("Plot 136 Alake Onile-Ere Crescent, Gbagada, Lagos") == 2 and "Cresent" not in text
    assert "1.5 Business days." in text and "5.6.10 Late charges." in text and "5.6.11" not in text


def test_prices_in_the_terms_are_what_the_platform_charges(plans):
    from apps.billing import pricing

    text = _terms_page()
    for slug in ("starter", "growth", "dedicated"):
        plan = plans[slug]
        monthly = pricing.plan_price(plan, "ng", "month").amount_minor // 100
        yearly = pricing.plan_price(plan, "ng", "year").amount_minor // 100
        assert f"{plan.name} ₦{monthly:,} a month" in text, slug
        assert f"{plan.name} ₦{yearly:,}" in text, slug


def test_timings_in_the_terms_are_what_the_platform_does(db):
    text = _terms_page()
    assert renewals.REMINDER_LEAD == timedelta(days=3) and renewals.YEARLY_REMINDER_LEAD == timedelta(days=14)
    assert "For monthly plans we send it three (3) days before the period ends, and for yearly plans fourteen (14) days before." in text
    assert renewals.GRACE_BUSINESS_DAYS == 5 and "grace period of five (5) business days" in text
    assert renewals.END_AFTER_PAUSE == timedelta(days=30) and "thirty (30) days after we paused your account" in text


def test_the_reminder_uses_the_wording_in_the_terms(plans, organization, owner):
    from apps.core.models import OutboxEvent

    _subscribe(organization, plans["growth"], ends_in=timedelta(days=2))
    renewals.run(timezone.now())
    body = OutboxEvent.objects.get().payload["body"]
    for part in ("Plans do not renew by themselves. To keep your plan for another month, pay ₦97,500 here:",
                 "If you do not want to continue, you do not need to do anything.",
                 "We never ask for card details, codes or passwords by message."):
        assert part in body
