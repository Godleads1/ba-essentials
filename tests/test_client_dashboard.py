"""Step 8: the client dashboard in the banda design, built from the business's own data."""
from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone

from apps.billing.models import Invoice, InvoiceStatus
from apps.core.client_status import label
from apps.tasks import dashboard
from apps.tasks.models import Task, TaskStatus


def _status(task, status, **extra):
    Task.objects.filter(pk=task.pk).update(status=status, **extra)


def test_dashboard_shows_this_business_only(client, owner, organization, make_task, other_owner,
                                            other_organization):
    mine = make_task(organization, owner, title="Our launch logo")
    make_task(other_organization, other_owner, title="Someone else's secret plan")
    body = client.get("/app/").content.decode()
    assert "Our launch logo" in body and "secret plan" not in body
    assert "Tasks sent this week" in body and "Recent requests" in body and mine.reference in body


def test_counts_use_client_groups(owner, organization, make_task):
    a, b, c = (make_task(organization, owner) for _ in range(3))
    _status(a, TaskStatus.IN_PROGRESS)
    _status(b, TaskStatus.INTERNAL_REVIEW)
    _status(c, TaskStatus.COMPLETED)
    tasks = Task.objects.filter(organization=organization)
    s = dashboard.stats(tasks, timezone.now())
    assert (s["total"], s["in_progress"], s["checking"], s["delivered"]) == (3, 1, 1, 1)


def test_waiting_on_you_picks_what_needs_the_client(client, owner, organization, make_task):
    t = make_task(organization, owner, title="Quote me")
    assert "Nothing right now" in client.get("/app/").content.decode()
    _status(t, TaskStatus.AWAITING_CLIENT_APPROVAL)
    body = client.get("/app/").content.decode()
    assert "Review and approve the quote" in body and f"/app/tasks/{t.pk}/" in body


def test_internal_statuses_are_not_shown_to_clients(owner, organization, make_task, client):
    t = make_task(organization, owner)
    _status(t, TaskStatus.REWORK_REQUIRED)
    body = client.get("/app/").content.decode()
    assert "Rework" not in body and label("rework_required") == "In progress"


def test_week_counts_today_and_leaves_future_days_empty(owner, organization, make_task):
    make_task(organization, owner)
    w = dashboard.week(Task.objects.filter(organization=organization), timezone.now())
    today = timezone.localtime().weekday()
    assert w["total"] == 1 and w["days"][today]["count"] == 1 and w["days"][today]["today"]
    assert all(d["count"] == 0 for d in w["days"][today + 1:])


def test_deadlines_are_open_requests_by_due_date(owner, organization, make_task):
    soon, later, done = (make_task(organization, owner) for _ in range(3))
    _status(soon, TaskStatus.IN_PROGRESS, due_date=date.today() + timedelta(days=1))
    _status(later, TaskStatus.IN_PROGRESS, due_date=date.today() + timedelta(days=9))
    _status(done, TaskStatus.COMPLETED, due_date=date.today())
    assert [t.pk for t in dashboard.deadlines(Task.objects.filter(organization=organization))] == [soon.pk, later.pk]


def test_unpaid_invoices_are_flagged(client, organization):
    Invoice.objects.create(number="INV-D8", organization=organization, amount_minor=1000, status=InvoiceStatus.ISSUED)
    assert "1 invoice to pay" in client.get("/app/").content.decode()


def test_plan_card_and_usage(client, subscription):
    body = client.get("/app/").content.decode()
    assert subscription.plan.name in body and "tasks used" in body and "Manage plan" in body


def test_no_plan(client):
    body = client.get("/app/").content.decode()
    assert "Pick a plan to start" in body and "See plans" in body


def test_group_filter_on_my_tasks(client, owner, organization, make_task):
    a = make_task(organization, owner, title="Alpha in review")
    make_task(organization, owner, title="Beta submitted")
    _status(a, TaskStatus.INTERNAL_REVIEW)
    body = client.get("/app/tasks/?group=checking").content.decode()
    assert "Alpha in review" in body and "Beta submitted" not in body
