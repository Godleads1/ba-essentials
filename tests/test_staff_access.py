"""Step 6: staff see client requests only when their role needs it, and every look is logged."""
from __future__ import annotations

import pytest

from apps.accounts.models import StaffRole
from apps.audit.models import AuditEvent
from apps.billing import fees
from apps.billing.models import Invoice, InvoiceStatus
from apps.communications.models import SupportTicket, SupportTicketStatus
from apps.files.policy import can_view_file
from apps.tasks.access import can_view_task, can_view_task_money, tasks_for
from apps.tasks.models import Task, TaskAttachment, TaskStatus

from .conftest import login_client, make_user, pdf_upload


@pytest.fixture
def support_user(db):
    return make_user("support@example.test", staff_role=StaffRole.SUPPORT, mfa=True)


def _set_status(task, status):
    Task.objects.filter(pk=task.pk).update(status=status)
    task.refresh_from_db()
    return task


def _detail(user, task):
    return login_client(user).get(f"/staff/tasks/{task.pk}/")


# ---------- who sees what ----------

def test_operations_see_every_request(ops_user, submitted_task):
    assert can_view_task(ops_user, submitted_task) and submitted_task in tasks_for(ops_user)


def test_reviewers_see_only_requests_in_review(reviewer, submitted_task):
    assert not can_view_task(reviewer, submitted_task)
    assert _detail(reviewer, submitted_task).status_code == 404
    _set_status(submitted_task, TaskStatus.INTERNAL_REVIEW)
    assert can_view_task(reviewer, submitted_task)
    assert list(tasks_for(reviewer)) == [submitted_task]
    _set_status(submitted_task, TaskStatus.DELIVERED)
    assert not can_view_task(reviewer, submitted_task)


def test_support_sees_requests_only_for_tickets_they_handle(support_user, submitted_task, owner, organization):
    assert not can_view_task(support_user, submitted_task)
    ticket = SupportTicket.objects.create(reference="SUP-9", organization=organization, created_by=owner,
                                          subject="Where is my logo?", status=SupportTicketStatus.OPEN)
    assert not can_view_task(support_user, submitted_task)  # not theirs yet
    SupportTicket.objects.filter(pk=ticket.pk).update(assigned_to=support_user)
    assert can_view_task(support_user, submitted_task)
    SupportTicket.objects.filter(pk=ticket.pk).update(status=SupportTicketStatus.RESOLVED)
    assert not can_view_task(support_user, submitted_task)  # access ends with the ticket


def test_support_sees_nothing_from_other_businesses(support_user, owner, organization, other_owner,
                                                     other_organization, make_task):
    theirs = make_task(other_organization, other_owner)
    SupportTicket.objects.create(reference="SUP-8", organization=organization, created_by=owner, subject="Hi",
                                 status=SupportTicketStatus.OPEN, assigned_to=support_user)
    assert not can_view_task(support_user, theirs)


def test_finance_sees_money_not_contents(finance, submitted_task, organization):
    assert not can_view_task(finance, submitted_task)
    assert not can_view_task_money(finance, submitted_task)
    assert _detail(finance, submitted_task).status_code == 404  # no money on it yet
    Invoice.objects.create(number="INV-S6", organization=organization, task=submitted_task,
                           amount_minor=500000, status=InvoiceStatus.ISSUED)
    r = _detail(finance, submitted_task)
    body = r.content.decode()
    assert r.status_code == 200 and "INV-S6" in body
    assert submitted_task.brief not in body and "invoices and third-party fees only" in body


def test_finance_can_open_fee_evidence_but_not_request_files(finance, ops_user, scoping_task, owner):
    draft = fees.draft_for(scoping_task, ops_user)
    fees.add_line(draft, ops_user, payee="CAC", description="Filing", amount_minor=1000000,
                  evidence=pdf_upload("CAC_Fee_Schedule.pdf"))
    line = draft.lines.first()
    assert can_view_file(finance, line.evidence)
    from apps.files.services import store_upload

    brief_file = store_upload(uploaded_file=pdf_upload("Secret_brief.pdf"), organization=scoping_task.organization,
                              user=owner)
    TaskAttachment.objects.create(task=scoping_task, file=brief_file, added_by=owner)
    assert not can_view_file(finance, brief_file)
    assert can_view_file(ops_user, brief_file)


def test_specialist_access_ends_with_the_assignment(specialist, submitted_task):
    assert not can_view_task(specialist, submitted_task)


def test_a_deactivated_staff_member_sees_nothing(ops_user, submitted_task):
    ops_user.is_active = False
    ops_user.save()
    assert not can_view_task(ops_user, submitted_task) and not tasks_for(ops_user).exists()


def test_clients_are_unaffected(owner, submitted_task, other_owner):
    assert can_view_task(owner, submitted_task) and not can_view_task(other_owner, submitted_task)


# ---------- every look is logged ----------

def test_opening_a_request_is_logged_with_the_client(ops_user, submitted_task):
    assert _detail(ops_user, submitted_task).status_code == 200
    event = AuditEvent.objects.get(action="staff.task_opened")
    assert event.actor == ops_user and event.organization_id == submitted_task.organization_id
    assert event.resource_id == submitted_task.reference
    assert event.metadata == {"view": "full", "role": "operations"}


def test_lists_and_searches_are_logged_without_the_search_words(ops_user, submitted_task):
    c = login_client(ops_user)
    c.get("/staff/queue/?q=launch")
    event = AuditEvent.objects.get(action="staff.list_viewed", resource_id="queue")
    assert event.metadata == {"rows": 1, "searched": "yes", "role": "operations"}
    assert "launch" not in str(event.metadata)


@pytest.mark.parametrize("path", ["/staff/", "/staff/clients/", "/staff/audit/", "/staff/finance/",
                                  "/staff/support/", "/staff/reviews/"])
def test_staff_pages_with_client_data_are_logged(path, db):
    admin = make_user("admin6@example.test", staff_role=StaffRole.ADMIN, mfa=True)
    assert login_client(admin).get(path).status_code == 200
    assert AuditEvent.objects.filter(action="staff.list_viewed", actor=admin).exists(), path


def test_clients_looking_at_their_own_requests_are_not_logged_as_staff(client, submitted_task):
    client.get(f"/app/tasks/{submitted_task.pk}/")
    assert not AuditEvent.objects.filter(action__startswith="staff.").exists()
