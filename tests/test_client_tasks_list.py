"""Step 8: My tasks in the banda design: server-side search, filters and pages."""
from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone

from apps.core.templatetags.icons import icon
from apps.tasks.models import Task, TaskStatus


def _status(task, status, **extra):
    Task.objects.filter(pk=task.pk).update(status=status, **extra)


def test_lists_only_this_business(client, owner, organization, make_task, other_owner, other_organization):
    make_task(organization, owner, title="Ours")
    make_task(other_organization, other_owner, title="Theirs")
    body = client.get("/app/tasks/").content.decode()
    assert "Ours" in body and "Theirs" not in body and "What each status means" in body


def test_search_category_status_and_date_filters(client, owner, organization, make_task):
    a = make_task(organization, owner, title="Alpha logo")
    b = make_task(organization, owner, title="Beta report")
    _status(a, TaskStatus.NEEDS_INFORMATION)
    Task.objects.filter(pk=b.pk).update(created_at=timezone.now() - timedelta(days=40))
    assert "Beta report" not in client.get("/app/tasks/?q=alpha").content.decode()
    body = client.get("/app/tasks/?group=input").content.decode()
    assert "Alpha logo" in body and "Beta report" not in body
    body = client.get("/app/tasks/?days=30").content.decode()
    assert "Alpha logo" in body and "Beta report" not in body
    slug = a.service_version.definition.category.slug
    assert "Alpha logo" in client.get(f"/app/tasks/?category={slug}").content.decode()
    assert "Alpha logo" not in client.get("/app/tasks/?category=nope").content.decode()


def test_counts_and_active_group(client, owner, organization, make_task):
    a, b = make_task(organization, owner), make_task(organization, owner)
    _status(a, TaskStatus.IN_PROGRESS)
    _status(b, TaskStatus.INTERNAL_REVIEW)
    body = client.get("/app/tasks/?group=active").content.decode()
    assert a.reference in body and b.reference in body


def test_past_due_is_flagged_and_priority_reads_urgent(client, owner, organization, make_task):
    t = make_task(organization, owner)
    _status(t, TaskStatus.IN_PROGRESS, due_date=date.today() - timedelta(days=1), priority="high")
    body = client.get("/app/tasks/").content.decode()
    assert "Past due" in body and "Urgent" in body


def test_ten_to_a_page_and_filters_kept(client, owner, organization, make_task):
    for i in range(12):
        make_task(organization, owner, title=f"Request {i}")
    body = client.get("/app/tasks/?q=Request").content.decode()
    assert "Showing 1–10 of 12" in body and "q=Request&amp;page=2" in body
    assert "Showing 11–12 of 12" in client.get("/app/tasks/?q=Request&page=2").content.decode()


def test_empty_states(client):
    assert "No requests yet" in client.get("/app/tasks/").content.decode()
    assert "No tasks match these filters" in client.get("/app/tasks/?q=zzz").content.decode()


def test_icon_tag_escapes_and_hides_from_screen_readers():
    out = icon("plus", 'h-4" onload="x')
    assert 'aria-hidden="true"' in out and 'onload="x' not in out
