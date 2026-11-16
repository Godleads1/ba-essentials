"""The price follows where the work is for. A naira or Africa plan doesn't cover work for a
country outside Africa: it's quoted, or staff move the business to the international plan.
Staff confirm the country from the documents before any quote, and a card from another
country is flagged for review."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.billing import plan_rules
from apps.billing import services as billing
from apps.billing.models import Plan, Subscription
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.organizations.models import AccountFlag
from apps.organizations.services import resolve_flag
from apps.tasks.services import confirm_work_country
from apps.tasks.transitions import Action, transition

from .conftest import login_client


@pytest.fixture
def starter_plan(db, organization):
    call_command("seed_plans", stdout=open("/dev/null", "w"))
    organization.country = "NG"
    organization.save(update_fields=["country"])
    now = timezone.now()
    return Subscription.objects.create(
        organization=organization, plan=Plan.objects.get(slug="starter"),
        period_start=now - timedelta(days=1), period_end=now + timedelta(days=29),
    )


# ---------- recording the country ----------

def test_a_request_defaults_to_the_business_country(make_task, organization, owner):
    organization.country = "GH"
    organization.save(update_fields=["country"])
    assert make_task(organization, owner).work_country == "GH"


def test_the_client_can_say_the_work_is_for_another_country(make_task, organization, owner):
    assert make_task(organization, owner, work_country="US").work_country == "US"
    with pytest.raises(ValidationFailure):
        make_task(organization, owner, work_country="XX")


def test_a_service_with_a_fixed_country_overrides_the_client(make_task, organization, owner, service_version):
    service_version.definition.work_country = "US"
    service_version.definition.save(update_fields=["work_country"])
    assert make_task(organization, owner, work_country="NG").work_country == "US"


def test_the_request_form_asks_the_country(client, organization):
    organization.country = "NG"
    organization.save(update_fields=["country"])
    body = client.get("/app/tasks/new/").content.decode()
    assert "Which country is this work for?" in body and 'value="NG" selected' in body


# ---------- what the plan covers ----------

def test_naira_plan_work_outside_africa_is_quoted_not_covered(starter_plan, make_task, organization, owner):
    task = make_task(organization, owner, work_country="GB")
    assert plan_rules.outside_plan_region(task)
    assert billing.reserve_allowance(task) is False
    assert billing.allowance_remaining(organization) == 7
    assert AuditEvent.objects.filter(action="billing.allowance_skipped_work_country").exists()


def test_africa_work_is_covered(starter_plan, make_task, organization, owner):
    for code in ("NG", "GH", "KE"):
        task = make_task(organization, owner, work_country=code)
        assert not plan_rules.outside_plan_region(task)
        assert billing.reserve_allowance(task) is True


def test_the_international_plan_covers_work_anywhere(starter_plan, make_task, organization, owner):
    organization.international_plan = True
    organization.save(update_fields=["international_plan"])
    task = make_task(organization, owner, work_country="US")
    assert not plan_rules.outside_plan_region(task)
    assert billing.reserve_allowance(task) is True


def test_the_client_sees_why_work_outside_africa_is_quoted(starter_plan, make_task, organization, owner, client):
    task = make_task(organization, owner, work_country="GB")
    body = client.get(f"/app/tasks/{task.pk}/").content.decode()
    assert "This work is for United Kingdom, outside Africa" in body and "One-off fee first" in body


# ---------- staff confirm before quoting ----------

def test_no_quote_before_the_country_is_confirmed(submitted_task, ops_user, quote_approver):
    task = transition(task=submitted_task, action=Action.TRIAGE, actor=ops_user)
    task = transition(task=task, action=Action.SCOPE, actor=quote_approver)
    with pytest.raises(ValidationFailure, match="Confirm which country"):
        transition(task=task, action=Action.SEND_QUOTE, actor=quote_approver)


def test_staff_correct_the_country_with_a_reason(submitted_task, ops_user, quote_approver, organization):
    organization.country = "NG"
    organization.save(update_fields=["country"])
    submitted_task.work_country = "NG"
    submitted_task.save(update_fields=["work_country"])
    task = transition(task=submitted_task, action=Action.TRIAGE, actor=ops_user)
    task = transition(task=task, action=Action.SCOPE, actor=quote_approver)
    with pytest.raises(ValidationFailure, match="Say why"):
        confirm_work_country(task, quote_approver, "US")
    task = confirm_work_country(task, quote_approver, "US", "Delaware company agreement")
    assert task.work_country == "US" and task.work_country_confirmed_by == quote_approver
    event = AuditEvent.objects.get(action="task.work_country_changed")
    assert event.metadata["from"] == "NG" and event.metadata["to"] == "US"


def test_only_scoping_staff_can_confirm(scoping_task, specialist, owner):
    for user in (specialist, owner):
        with pytest.raises(PermissionFailure):
            confirm_work_country(scoping_task, user, "NG")


def test_a_fixed_service_country_cannot_be_changed(scoping_task, quote_approver, service_version):
    service_version.definition.work_country = "US"
    service_version.definition.save(update_fields=["work_country"])
    with pytest.raises(ValidationFailure, match="always for one country"):
        confirm_work_country(scoping_task, quote_approver, "NG", "wrong")


def test_staff_page_confirms_the_country(submitted_task, ops_user, quote_approver):
    task = transition(task=submitted_task, action=Action.TRIAGE, actor=ops_user)
    task = transition(task=task, action=Action.SCOPE, actor=quote_approver)
    c = login_client(quote_approver)
    assert "Confirm before quoting" in c.get(f"/staff/tasks/{task.pk}/").content.decode()
    c.post(f"/staff/tasks/{task.pk}/work-country/", {"country": "GB", "reason": "UK supplier contract"})
    task.refresh_from_db()
    assert task.work_country == "GB" and task.work_country_confirmed_at is not None


# ---------- card from another country ----------

def test_a_card_from_another_country_is_flagged_not_blocked(organization):
    from apps.billing.models import Invoice

    organization.country = "NG"
    organization.save(update_fields=["country"])
    invoice = Invoice.objects.create(number="INV-T-1", organization=organization, amount_minor=100, currency="NGN")
    billing._flag_card_country(invoice, {"card_country": "GB"})
    billing._flag_card_country(invoice, {"card_country": "NG"})  # same country: no flag
    flags = AccountFlag.objects.filter(organization=organization)
    assert flags.count() == 1 and "GB" in flags.get().detail


def test_staff_resolve_a_flag_with_a_note(organization, quote_approver, specialist):
    flag = AccountFlag.objects.create(organization=organization, kind="card_country", detail="UK card")
    with pytest.raises(PermissionFailure):
        resolve_flag(flag, specialist, "looked")
    with pytest.raises(ValidationFailure):
        resolve_flag(flag, quote_approver, "")
    resolve_flag(flag, quote_approver, "Owner lives in London, business is in Lagos")
    flag.refresh_from_db()
    assert flag.resolved_by == quote_approver
    assert AuditEvent.objects.filter(action="organization.flag_resolved").exists()


def test_manifest_sets_a_service_country(db, tmp_path):
    import json

    from apps.catalogue.models import ServiceDefinition

    path = tmp_path / "c.json"
    path.write_text(json.dumps({"categories": [{"name": "Legal & IP Review", "slug": "legal", "services": [{
        "name": "US trademark search", "slug": "us-tm", "work_country": "US",
        "scope_summary": "Search the USPTO register.", "deliverables_summary": "A report."}]}]}))
    call_command("bootstrap_catalogue", str(path), stdout=open("/dev/null", "w"))
    assert ServiceDefinition.objects.get(slug="us-tm").work_country == "US"


def test_resolve_form_with_a_bad_return_address_does_not_crash(organization, quote_approver):
    flag = AccountFlag.objects.create(organization=organization, kind="card_country", detail="UK card")
    response = login_client(quote_approver).post(
        f"/staff/flags/{flag.pk}/resolve/", {"resolution": "Checked with the owner", "task": "not-a-uuid"}
    )
    assert response.status_code == 302 and response["Location"].endswith("/staff/clients/")
    flag.refresh_from_db()
    assert flag.resolved_at is not None
