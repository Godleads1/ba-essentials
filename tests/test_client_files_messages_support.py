"""Deliverables & Files, Messages and Help & Support in the banda design."""
from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from apps.communications import services as communications
from apps.communications.models import SupportTicketStatus
from apps.files import services as file_services
from apps.files.models import FilePurpose, ScanStatus
from apps.reviews.models import Deliverable, DeliverableVersion, DeliverableVersionStatus
from apps.tasks.models import TaskAttachment, TaskStatus

from .conftest import pdf_upload


def _deliver(task, owner, name="report-synthetic.pdf"):
    f = file_services.store_upload(organization=task.organization, user=owner, uploaded_file=pdf_upload(name),
                                   purpose=FilePurpose.DELIVERABLE)
    deliverable = Deliverable.objects.create(task=task)
    DeliverableVersion.objects.create(deliverable=deliverable, version_number=1, file=f, submitted_by=owner,
                                      status=DeliverableVersionStatus.RELEASED, released_at=timezone.now())
    return f


def _attach(task, owner, name="brief-synthetic.pdf"):
    f = file_services.store_upload(organization=task.organization, user=owner, uploaded_file=pdf_upload(name),
                                   purpose=FilePurpose.ATTACHMENT)
    TaskAttachment.objects.create(task=task, file=f, added_by=owner)
    return f


# ---------- Deliverables & Files ----------

def test_files_page_lists_delivered_and_uploaded_files_with_filters(client, owner, submitted_task):
    _deliver(submitted_task, owner)
    _attach(submitted_task, owner)

    page = client.get(reverse("files-home"))
    assert page.templates[0].name == "banda/app/files.html"
    body = page.content.decode()
    assert "report-synthetic.pdf" in body and "brief-synthetic.pdf" in body
    assert page.context["counts"] == {"all": 2, "delivered": 1, "waiting": 0, "uploaded": 1}

    only_uploads = client.get(reverse("files-home"), {"kind": "uploaded"}).content.decode()
    assert "brief-synthetic.pdf" in only_uploads and "report-synthetic.pdf" not in only_uploads

    search = client.get(reverse("files-home"), {"q": "REPORT"}).content.decode()
    assert "report-synthetic.pdf" in search and "brief-synthetic.pdf" not in search

    by_type = client.get(reverse("files-home"), {"type": "sheet"})
    assert by_type.context["page"].paginator.count == 0


def test_delivered_work_waiting_on_approval_is_flagged(client, owner, submitted_task):
    _deliver(submitted_task, owner)
    submitted_task.status = TaskStatus.DELIVERED
    submitted_task.save(update_fields=["status"])

    page = client.get(reverse("files-home"))
    assert page.context["counts"]["waiting"] == 1
    assert "Needs your approval" in page.content.decode()


def test_a_file_still_being_checked_has_no_download_link(client, owner, submitted_task):
    f = _attach(submitted_task, owner)
    type(f).objects.filter(pk=f.pk).update(scan_status=ScanStatus.PENDING)

    body = client.get(reverse("files-home")).content.decode()
    assert "Being checked" in body
    assert reverse("file-download", args=[f.pk]) not in body


def test_files_page_never_shows_another_business(client, owner, submitted_task, make_task, other_owner,
                                                 other_organization):
    other = make_task(other_organization, other_owner)
    _deliver(other, other_owner, "other-tenant-synthetic.pdf")
    assert "other-tenant-synthetic.pdf" not in client.get(reverse("files-home")).content.decode()


def test_files_are_paged_ten_at_a_time(client, owner, submitted_task):
    for i in range(12):
        _attach(submitted_task, owner, f"page-{i:02d}-synthetic.pdf")
    first = client.get(reverse("files-home"))
    assert len(first.context["page"].object_list) == 10
    second = client.get(reverse("files-home"), {"page": 2})
    assert len(second.context["page"].object_list) == 2


# ---------- Messages ----------

def test_messages_page_opens_the_latest_conversation(client, owner, submitted_task):
    communications.post_task_message(task=submitted_task, author=owner, body="Synthetic question for the team.")

    page = client.get(reverse("messages-home"))
    assert page.templates[0].name == "banda/app/messages.html"
    assert page.context["selected"] == submitted_task
    body = page.content.decode()
    assert "Synthetic question for the team." in body
    assert reverse("task-message-post", args=[submitted_task.pk]) in body


def test_messages_page_hides_internal_notes(client, owner, ops_user, submitted_task):
    communications.post_task_message(task=submitted_task, author=owner, body="Synthetic client line.")
    communications.post_task_message(task=submitted_task, author=ops_user, body="Synthetic internal note.",
                                     internal=True)
    body = client.get(reverse("messages-home"), {"task": str(submitted_task.pk)}).content.decode()
    assert "Synthetic client line." in body
    assert "Synthetic internal note." not in body


def test_messages_page_refuses_another_business_or_a_bad_id(client, make_task, other_owner, other_organization):
    other = make_task(other_organization, other_owner)
    assert client.get(reverse("messages-home"), {"task": str(other.pk)}).status_code == 404
    assert client.get(reverse("messages-home"), {"task": "not-a-uuid"}).status_code == 404


def test_sending_from_messages_returns_to_messages_and_only_there(client, submitted_task):
    url = reverse("task-message-post", args=[submitted_task.pk])
    back = client.post(url, {"body": "Synthetic reply from messages.", "back": "messages"})
    assert back.status_code == 302
    assert back["Location"].startswith(f"{reverse('messages-home')}?task={submitted_task.pk}")

    elsewhere = client.post(url, {"body": "Synthetic second reply.", "back": "https://example.com/"})
    assert elsewhere["Location"].startswith(reverse("task-detail", args=[submitted_task.pk]))


# ---------- Help & Support ----------

def _ticket(organization, owner, subject="Synthetic billing question"):
    return communications.create_ticket(organization=organization, author=owner, subject=subject,
                                        body="Please look at this synthetic question.")


def test_help_page_lists_requests_and_filters_them(client, owner, organization):
    _ticket(organization, owner, "Synthetic open request")
    done = _ticket(organization, owner, "Synthetic finished request")
    communications.close_ticket_by_client(done, owner)

    page = client.get(reverse("support-home"))
    assert page.templates[0].name == "banda/app/support_list.html"
    body = page.content.decode()
    assert "Synthetic open request" in body and "Synthetic finished request" in body
    assert "Common questions" in body

    active = client.get(reverse("support-home"), {"status": "active"}).content.decode()
    assert "Synthetic open request" in active and "Synthetic finished request" not in active


def test_help_page_never_shows_another_business(client, other_owner, other_organization):
    _ticket(other_organization, other_owner, "Synthetic other business request")
    assert "Synthetic other business request" not in client.get(reverse("support-home")).content.decode()


def test_new_request_page_starts_the_subject_from_a_topic(client):
    page = client.get(reverse("support-new"), {"topic": "billing"})
    assert page.templates[0].name == "banda/app/support_new.html"
    assert page.context["form"]["subject"].value() == "Billing & invoices: "
    unknown = client.get(reverse("support-new"), {"topic": "<script>"})
    assert unknown.context["form"]["subject"].value() in (None, "")


def test_new_request_shows_field_errors(client):
    page = client.post(reverse("support-new"), {"subject": "Hi", "priority": "medium", "body": "short"})
    assert page.status_code == 200
    body = page.content.decode()
    assert "Give the ticket a clearer subject." in body


def test_request_page_shows_status_and_hides_staff_names(client, owner, organization, ops_user):
    ticket = _ticket(organization, owner)
    ticket.assigned_to = ops_user
    ticket.save(update_fields=["assigned_to"])

    body = client.get(reverse("support-detail", args=[ticket.pk])).content.decode()
    assert "Open" in body and "Close this request" in body
    assert ops_user.email not in body


def test_closed_request_has_no_reply_form(client, owner, organization):
    ticket = _ticket(organization, owner)
    communications.close_ticket_by_client(ticket, owner)
    ticket.refresh_from_db()
    assert ticket.status == SupportTicketStatus.CLOSED
    body = client.get(reverse("support-detail", args=[ticket.pk])).content.decode()
    assert "This request is closed." in body
    assert 'name="body"' not in body
