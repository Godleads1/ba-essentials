"""Monthly summary page and email for Growth and Dedicated (pricing page promise)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.billing.models import Plan, Subscription, SubscriptionStatus, UsageKind, UsageLedger
from apps.communications.models import SupportTicket
from apps.core.business_time import business_hours_between
from apps.core.models import OutboxEvent
from apps.reports import emails, summary
from apps.reports.models import MonthlySummarySent
from apps.tasks.models import Task, TaskStatus, TaskStatusEvent

LAGOS = ZoneInfo("Africa/Lagos")
OCT = date(2026, 10, 1)
NOW = datetime(2026, 11, 26, 12, tzinfo=LAGOS)
pytestmark = pytest.mark.django_db


def at(d, h=10):
    return datetime(2026, 10, d, h, tzinfo=LAGOS)


def _plan(organization, slug="growth", rank=2, allowance=20):
    plan = Plan.objects.create(name=slug.title(), slug=slug, rank=rank, monthly_task_allowance=allowance,
                               price_minor=0, legal_review_allowance=1)
    now = timezone.now()
    return Subscription.objects.create(organization=organization, plan=plan, status=SubscriptionStatus.ACTIVE,
                                       period_start=now - timedelta(days=1), period_end=now + timedelta(days=29))


def _task(make_task, organization, owner, title, sent, delivered=None):
    task = make_task(organization, owner, title=title)
    Task.objects.filter(pk=task.pk).update(created_at=sent)
    if delivered:
        Task.objects.filter(pk=task.pk).update(status=TaskStatus.DELIVERED)
        event = TaskStatusEvent.objects.create(task=task, from_status=TaskStatus.APPROVED_FOR_RELEASE,
                                               to_status=TaskStatus.DELIVERED, action="release")
        TaskStatusEvent.objects.filter(pk=event.pk).update(created_at=delivered)
    task.refresh_from_db()
    return task


@pytest.fixture
def october(make_task, organization, owner):
    from apps.organizations.models import Organization

    Organization.objects.filter(pk=organization.pk).update(created_at=datetime(2026, 9, 1, tzinfo=LAGOS))
    a = _task(make_task, organization, owner, "Supplier contract review", at(5), at(7))      # Mon -> Wed: 2 days
    b = _task(make_task, organization, owner, "CAC annual returns", at(12), at(16))         # Mon -> Fri: 4 days
    _task(make_task, organization, owner, "Still in progress", at(20))
    _task(make_task, organization, owner, "Sent in September", datetime(2026, 9, 28, 10, tzinfo=LAGOS), at(1))
    for task, kind in ((a, UsageKind.ALLOWANCE), (b, UsageKind.ALLOWANCE), (a, UsageKind.LEGAL)):
        row = UsageLedger.objects.create(organization=organization, task=task, kind=kind, period_start=at(1))
        UsageLedger.objects.filter(pk=row.pk).update(created_at=at(5))
    on_time = SupportTicket.objects.create(reference="SUP-1", organization=organization, subject="On time",
                                           reply_due_at=at(6, 14), first_reply_at=at(6, 11))
    late = SupportTicket.objects.create(reference="SUP-2", organization=organization, subject="Late",
                                        reply_due_at=at(8, 14), first_reply_at=at(9, 11))
    SupportTicket.objects.filter(pk__in=[on_time.pk, late.pk]).update(created_at=at(6, 9))
    organization.refresh_from_db()
    return organization


def test_business_hours_between():
    assert business_hours_between(at(5, 9), at(5, 17)) == 8           # one full Monday
    assert business_hours_between(at(9, 16), at(12, 10)) == 2          # Friday 4pm -> Monday 10am
    assert business_hours_between(at(5, 12), at(5, 11)) == 0


def test_summary_figures(october):
    _plan(october)
    s = summary.build(october, OCT, now=NOW)
    assert s.sent == 3                     # the September request doesn't count as sent in October
    assert s.delivered == 3                # but its first delivery (1 Oct) does
    assert [r["task"].title for r in s.delivered_tasks][-2:] == ["Supplier contract review", "CAC annual returns"]
    assert s.plan_used == 2 and s.legal_used == 1 and s.allowance == 20 and s.unused == 18
    assert s.support_on_time == 1 and s.support_counted == 2 and s.support_percent == 50
    assert s.complete and not s.empty
    assert dict(s.by_service)  # grouped by the service's category


def test_turnaround_counts_business_days(make_task, organization, owner):
    _task(make_task, organization, owner, "Two days", at(5), at(7))
    _task(make_task, organization, owner, "Four days", at(12), at(16))
    assert summary.build(organization, OCT, now=NOW).turnaround_days == 3.0


def test_an_empty_month(organization):
    _plan(organization)
    s = summary.build(organization, OCT, now=NOW)
    assert s.empty and s.turnaround_days is None and s.support_percent is None


# ---------- page ----------

def test_starter_and_no_plan_see_what_it_offers(client, organization):
    page = client.get("/app/summary/").content.decode()
    assert "Monthly summaries come with Growth and Dedicated" in page
    _plan(organization, "starter", 1, 7)
    assert "come with Growth and Dedicated" in client.get("/app/summary/").content.decode()


def test_growth_sees_the_month(client, october):
    _plan(october)
    page = client.get("/app/summary/?month=2026-10").content.decode()
    assert "October 2026 summary" in page and "Supplier contract review" in page
    assert "Support replies on time" in page and "50%" in page


def test_bad_or_out_of_range_months_fall_back(client, october):
    _plan(october)
    for raw in ("nonsense", "2020-01", "2099-01", "2026-13"):
        assert client.get(f"/app/summary/?month={raw}").status_code == 200


def test_another_business_sees_only_its_own(other_client, other_organization, october):
    _plan(october)
    _plan(other_organization, "dedicated", 3, 30)
    page = other_client.get("/app/summary/?month=2026-10").content.decode()
    assert "Supplier contract review" not in page


def test_dashboard_links_to_it_for_growth(client, organization):
    assert "Monthly summary" not in client.get("/app/").content.decode()
    _plan(organization)
    assert "/app/summary/" in client.get("/app/").content.decode()


# ---------- email ----------

def _owner_emails():
    return [e.payload["to"][0] for e in OutboxEvent.objects.filter(topic=OutboxEvent.Topic.EMAIL)
            if "summary" in e.payload["subject"]]


def test_email_goes_to_owners_once(october, owner, member):
    _plan(october)
    counts = emails.send_for_month(OCT, now=NOW)
    assert counts["queued"] == 1 and _owner_emails() == [owner.email]          # members don't get it
    body = OutboxEvent.objects.get(payload__to=[owner.email]).payload["body"]
    assert "Requests delivered: 3" in body and "/app/summary/?month=2026-10" in body
    assert "Supplier contract review" not in body                               # no request names
    assert emails.send_for_month(OCT, now=NOW)["already_sent"] == 1
    assert len(_owner_emails()) == 1 and MonthlySummarySent.objects.count() == 1


def test_email_respects_the_switch_and_empty_months(october, organization, owner):
    _plan(october)
    owner.summary_by_email = False
    owner.save(update_fields=["summary_by_email"])
    assert emails.send_for_month(OCT, now=NOW)["no_recipient"] == 1
    assert emails.send_for_month(date(2026, 8, 1), now=NOW)["skipped_empty"] == 1
    assert _owner_emails() == []


def test_starter_gets_no_email(october):
    _plan(october, "starter", 1, 7)
    emails.send_for_month(OCT, now=NOW)
    assert _owner_emails() == []


def test_owner_can_turn_the_email_off(client, owner):
    client.post("/app/security/preferences/", {"updates_by_email": "on"})
    owner.refresh_from_db()
    assert owner.summary_by_email is False and owner.updates_by_email is True


def test_command_refuses_an_unfinished_month():
    from django.core.management import CommandError, call_command

    with pytest.raises(CommandError):
        call_command("send_monthly_summaries", month=f"{summary.this_month():%Y-%m}")
