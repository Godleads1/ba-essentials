from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import StaffRole
from apps.communications import services as communications
from apps.communications.models import (
    Notification,
    NotificationKind,
    SupportTicket,
    SupportTicketStatus,
    TaskMessage,
)
from apps.tasks.models import Assignment, AssignmentStatus

from .conftest import login_client, make_user


@pytest.fixture
def support_user(db):
    return make_user(
        "support@example.test", staff_role=StaffRole.SUPPORT, mfa=True, is_staff=True
    )


@pytest.fixture
def support_client(support_user):
    return login_client(support_user)


def test_client_message_notifies_staff_and_appears_in_thread(
    client, owner, ops_user, submitted_task
):
    response = client.post(
        reverse("task-message-post", args=[submitted_task.pk]),
        {"body": "Synthetic follow-up for the team."},
    )

    assert response.status_code == 302
    message = TaskMessage.objects.get(task=submitted_task)
    assert message.author == owner
    assert message.author_role == "client"
    assert message.is_internal is False
    assert Notification.objects.filter(user=ops_user, task=submitted_task).exists()

    page = client.get(reverse("task-detail", args=[submitted_task.pk]))
    assert "Synthetic follow-up for the team." in page.content.decode()


def test_other_tenant_cannot_post_to_a_task_thread(other_client, submitted_task):
    response = other_client.post(
        reverse("task-message-post", args=[submitted_task.pk]),
        {"body": "Cross-tenant attempt."},
    )

    assert response.status_code == 404
    assert TaskMessage.objects.count() == 0


def test_internal_note_is_hidden_from_the_client_and_sends_no_client_notification(
    client, staff_client, owner, submitted_task
):
    response = staff_client.post(
        reverse("staff-task-message-post", args=[submitted_task.pk]),
        {"body": "Internal-only synthetic note.", "internal": "1"},
    )

    assert response.status_code == 302
    message = TaskMessage.objects.get(task=submitted_task)
    assert message.is_internal is True
    assert message.author_role == "staff"

    client_page = client.get(reverse("task-detail", args=[submitted_task.pk]))
    assert "Internal-only synthetic note." not in client_page.content.decode()
    assert not Notification.objects.filter(user=owner).exists()

    staff_page = staff_client.get(reverse("staff-task-detail", args=[submitted_task.pk]))
    assert "Internal-only synthetic note." in staff_page.content.decode()


def test_client_post_cannot_request_an_internal_note(client, submitted_task):
    client.post(
        reverse("task-message-post", args=[submitted_task.pk]),
        {"body": "Synthetic message.", "internal": "1"},
    )

    assert TaskMessage.objects.get(task=submitted_task).is_internal is False


def test_staff_reply_notifies_the_client(client, staff_client, owner, submitted_task):
    staff_client.post(
        reverse("staff-task-message-post", args=[submitted_task.pk]),
        {"body": "The team has reviewed this request."},
    )

    assert Notification.objects.filter(
        user=owner, kind=NotificationKind.TASK_MESSAGE, task=submitted_task
    ).exists()
    page = client.get(reverse("task-detail", args=[submitted_task.pk]))
    assert "The team has reviewed this request." in page.content.decode()


def test_assigned_specialist_is_the_staff_notification_target(
    client, owner, specialist, ops_user, submitted_task
):
    Assignment.objects.create(
        task=submitted_task,
        specialist=specialist,
        assigned_by=ops_user,
        status=AssignmentStatus.ACTIVE,
    )

    client.post(
        reverse("task-message-post", args=[submitted_task.pk]),
        {"body": "Synthetic message for the specialist."},
    )

    assert Notification.objects.filter(user=specialist, task=submitted_task).exists()
    assert not Notification.objects.filter(user=ops_user, task=submitted_task).exists()


def test_support_ticket_flow_is_tenant_scoped(
    client, other_client, support_client, support_user, owner, submitted_task
):
    response = client.post(
        reverse("support-new"),
        {
            "subject": "Synthetic invoice question",
            "priority": "medium",
            "body": "Please explain the synthetic invoice line item.",
        },
    )
    assert response.status_code == 302

    ticket = SupportTicket.objects.get()
    assert ticket.organization == submitted_task.organization
    assert Notification.objects.filter(user=support_user, ticket=ticket).exists()
    assert other_client.get(reverse("support-detail", args=[ticket.pk])).status_code == 404

    queue = support_client.get(reverse("staff-support"))
    assert "Synthetic invoice question" in queue.content.decode()

    support_client.post(
        reverse("staff-support-detail", args=[ticket.pk]),
        {"action": "reply", "body": "Internal triage note.", "internal": "1"},
    )
    support_client.post(
        reverse("staff-support-detail", args=[ticket.pk]),
        {"action": "reply", "body": "Synthetic public reply."},
    )

    client_page = client.get(reverse("support-detail", args=[ticket.pk]))
    body = client_page.content.decode()
    assert "Synthetic public reply." in body
    assert "Internal triage note." not in body
    assert Notification.objects.filter(user=owner, ticket=ticket).exists()


def test_client_can_close_a_ticket_but_not_reply_afterwards(client, owner, organization):
    ticket = communications.create_ticket(
        organization=organization,
        author=owner,
        subject="Synthetic close test",
        body="Please close this synthetic ticket.",
    )

    response = client.post(reverse("support-close", args=[ticket.pk]))
    assert response.status_code == 302
    ticket.refresh_from_db()
    assert ticket.status == SupportTicketStatus.CLOSED

    client.post(
        reverse("support-detail", args=[ticket.pk]),
        {"body": "Synthetic late reply."},
    )
    assert ticket.messages.count() == 1


def test_reviewer_cannot_reach_the_support_queue(db, reviewer):
    staff = login_client(reviewer)

    response = staff.get(reverse("staff-support"))

    assert response.status_code == 302
    assert response["Location"] == reverse("staff-dashboard")


def test_notifications_can_be_marked_read(client, owner, submitted_task, ops_user):
    communications.notify(
        [owner],
        organization_id=submitted_task.organization_id,
        kind=NotificationKind.TASK_MESSAGE,
        title="Synthetic notification",
        task=submitted_task,
    )

    response = client.post(reverse("notifications-read"))

    assert response.status_code == 302
    assert not Notification.objects.filter(user=owner, read_at__isnull=True).exists()
