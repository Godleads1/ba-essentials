"""Step 8: Task detail and Submit a Task in the banda design."""
from __future__ import annotations

from apps.communications.models import TaskMessage
from apps.tasks.dashboard import timeline
from apps.tasks.models import Task, TaskStatus


def _status(task, status, **extra):
    Task.objects.filter(pk=task.pk).update(status=status, **extra)
    task.refresh_from_db()
    return task


# ---------- task detail ----------

def test_detail_shows_the_request_in_banda(client, submitted_task):
    body = client.get(f"/app/tasks/{submitted_task.pk}/").content.decode()
    assert "/static/css/app.css" not in body and "Back to My Tasks" in body
    assert submitted_task.title in body and "Request details" in body and "Progress" in body


def test_other_businesses_cannot_open_it(other_client, submitted_task):
    assert other_client.get(f"/app/tasks/{submitted_task.pk}/").status_code == 404


def test_staff_notes_never_show_and_my_messages_are_mine(client, owner, submitted_task, ops_user):
    TaskMessage.objects.create(task=submitted_task, organization=submitted_task.organization, author=ops_user,
                               author_role="staff", body="INTERNAL NOTE", is_internal=True)
    TaskMessage.objects.create(task=submitted_task, organization=submitted_task.organization, author=owner,
                               author_role="client", body="Here is the logo")
    body = client.get(f"/app/tasks/{submitted_task.pk}/").content.decode()
    assert "INTERNAL NOTE" not in body and "Here is the logo" in body and ">You<" in body


def test_needs_info_shows_the_question_and_answer_form(client, submitted_task):
    _status(submitted_task, TaskStatus.NEEDS_INFORMATION, needs_info_question="Which colours?")
    body = client.get(f"/app/tasks/{submitted_task.pk}/").content.decode()
    assert "We need something from you" in body and "Which colours?" in body and 'name="information"' in body


def test_cancelled_request_closes_messaging(client, submitted_task):
    _status(submitted_task, TaskStatus.CANCELLED)
    body = client.get(f"/app/tasks/{submitted_task.pk}/").content.decode()
    assert "messaging is closed" in body and 'name="body"' not in body


def test_timeline_steps(submitted_task):
    steps = timeline(submitted_task, [])
    assert [s["state"] for s in steps] == ["now", "next", "next", "next", "next"]
    t = _status(submitted_task, TaskStatus.NEEDS_INFORMATION)
    states = [s["state"] for s in timeline(t, [])]
    assert "wait" in states and states[:2] == ["done", "done"]
    t = _status(submitted_task, TaskStatus.COMPLETED)
    assert all(s["state"] == "done" for s in timeline(t, []))
    t = _status(submitted_task, TaskStatus.CANCELLED)
    assert [s["state"] for s in timeline(t, [])] == ["done", "stop"]


# ---------- submit a task ----------

def test_submit_page_and_category_filter(client, service_version):
    category = service_version.definition.category
    body = client.get("/app/tasks/new/").content.decode()
    assert "1. Choose a service category" in body and "Brand &amp; Design" in body and "Before you upload" in body
    body = client.get(f"/app/tasks/new/?category={category.slug}").content.decode()
    assert 'aria-current="true"' in body and "Services in Brand &amp; Design" in body


def test_submitting_creates_the_request(client, owner, organization, service_version):
    r = client.post("/app/tasks/new/", {
        "service_version": service_version.pk, "title": "New brand guide",
        "brief": "Please create a short brand guide for our launch.", "priority": "high",
        "work_country": "NG",
    })
    task = Task.objects.get(title="New brand guide")
    assert r.status_code == 302 and r["Location"] == f"/app/tasks/{task.pk}/"
    assert task.organization == organization and task.priority == "high"


def test_errors_are_shown_next_to_fields(client, service_version):
    body = client.post("/app/tasks/new/", {"service_version": service_version.pk, "title": "ab", "brief": "short",
                                            "priority": "medium", "work_country": "NG"}).content.decode()
    assert "Give the request a clearer title." in body and "Add a little more detail" in body
    assert 'aria-invalid="true"' in body and "short" in body  # what they typed is kept
