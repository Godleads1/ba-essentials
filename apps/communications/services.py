from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import StaffRole, User
from apps.accounts.permissions import SUPPORT_HANDLE, has_perm
from apps.audit import services as audit_service
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.core.sequences import next_value
from apps.organizations.models import Membership, MembershipRole
from apps.tasks.access import can_view_task
from apps.tasks.models import TaskStatus

from .models import (
    AuthorRole,
    Notification,
    NotificationKind,
    SupportPriority,
    SupportTicket,
    SupportTicketStatus,
    TaskMessage,
    TicketMessage,
)

MAX_MESSAGE_LENGTH = 4000


def _staff_actor(user) -> bool:
    return bool(getattr(user, "staff_role", ""))


def _actor_name(user) -> str:
    return (getattr(user, "full_name", "") or getattr(user, "email", ""))[:150]


def _clean_body(body: str) -> str:
    text = (body or "").strip()
    if not text:
        raise ValidationFailure("Write a message before sending.")
    if len(text) > MAX_MESSAGE_LENGTH:
        raise ValidationFailure("That message is too long.")
    return text


def _client_recipients(task) -> list[User]:
    users = {
        membership.user
        for membership in Membership.objects.filter(
            organization_id=task.organization_id,
            role=MembershipRole.OWNER,
            user__is_active=True,
        ).select_related("user")
    }
    if task.created_by is not None and task.created_by.is_active:
        users.add(task.created_by)
    return list(users)


def _task_staff_recipients(task) -> list[User]:
    assignment = task.active_assignment
    if assignment is not None:
        return [assignment.specialist]
    return list(
        User.objects.filter(staff_role=StaffRole.OPERATIONS, is_active=True)
    )


def _support_recipients() -> list[User]:
    return list(
        User.objects.filter(
            staff_role__in=[StaffRole.SUPPORT, StaffRole.OPERATIONS, StaffRole.ADMIN],
            is_active=True,
        )
    )


def _ticket_client_recipients(ticket) -> list[User]:
    users = {
        membership.user
        for membership in Membership.objects.filter(
            organization_id=ticket.organization_id,
            role=MembershipRole.OWNER,
            user__is_active=True,
        ).select_related("user")
    }
    if ticket.created_by is not None and ticket.created_by.is_active:
        users.add(ticket.created_by)
    return list(users)


def notify(
    users,
    *,
    organization_id=None,
    kind: str,
    title: str,
    task=None,
    ticket=None,
    exclude=None,
) -> None:
    exclude_id = getattr(exclude, "pk", None)
    seen: set = set()
    rows = []
    for user in users:
        if user is None or not user.is_active or user.pk in seen:
            continue
        if exclude_id is not None and user.pk == exclude_id:
            continue
        seen.add(user.pk)
        rows.append(
            Notification(
                user=user,
                organization_id=organization_id,
                kind=kind,
                title=title[:160],
                task=task,
                ticket=ticket,
            )
        )
    if rows:
        Notification.objects.bulk_create(rows)


@transaction.atomic
def post_task_message(*, task, author, body: str, internal: bool = False) -> TaskMessage:
    if not can_view_task(author, task):
        raise PermissionFailure("You cannot access that request.")
    staff = _staff_actor(author)
    internal = bool(internal)
    if internal and not staff:
        raise PermissionFailure("Internal notes are only available to staff.")
    if not staff and task.status == TaskStatus.CANCELLED:
        raise ValidationFailure("This request is closed. Start a new request instead.")

    message = TaskMessage.objects.create(
        task=task,
        organization_id=task.organization_id,
        author=author,
        author_name=_actor_name(author),
        author_role=AuthorRole.STAFF if staff else AuthorRole.CLIENT,
        body=_clean_body(body),
        is_internal=internal,
    )
    audit_service.record(
        action="message.posted",
        actor=author,
        organization_id=task.organization_id,
        resource_type="task",
        resource_id=str(task.pk),
        metadata={"message_id": str(message.pk), "internal": internal},
    )

    if not internal:
        if staff:
            notify(
                _client_recipients(task),
                organization_id=task.organization_id,
                kind=NotificationKind.TASK_MESSAGE,
                title=f"New message on {task.reference}",
                task=task,
                exclude=author,
            )
        else:
            notify(
                _task_staff_recipients(task),
                organization_id=task.organization_id,
                kind=NotificationKind.TASK_MESSAGE,
                title=f"Client replied on {task.reference}",
                task=task,
                exclude=author,
            )
    return message


@transaction.atomic
def create_ticket(
    *,
    organization,
    author,
    subject: str,
    body: str,
    priority: str = SupportPriority.MEDIUM,
) -> SupportTicket:
    subject = (subject or "").strip()
    if len(subject) < 5:
        raise ValidationFailure("Give the ticket a clearer subject.")
    if len(subject) > 160:
        raise ValidationFailure("That subject is too long.")
    if priority not in SupportPriority.values:
        raise ValidationFailure("Choose a valid priority.")
    if not Membership.objects.filter(organization=organization, user=author).exists():
        raise PermissionFailure("You cannot open a ticket for that organization.")

    ticket = SupportTicket.objects.create(
        reference=f"SUP-{next_value('support.reference', start=1000)}",
        organization=organization,
        created_by=author,
        subject=subject,
        priority=priority,
        last_message_at=timezone.now(),
    )
    TicketMessage.objects.create(
        ticket=ticket,
        author=author,
        author_name=_actor_name(author),
        author_role=AuthorRole.CLIENT,
        body=_clean_body(body),
    )
    audit_service.record(
        action="support.opened",
        actor=author,
        organization_id=organization.pk,
        resource_type="support_ticket",
        resource_id=str(ticket.pk),
        metadata={"reference": ticket.reference},
    )
    notify(
        _support_recipients(),
        organization_id=organization.pk,
        kind=NotificationKind.SUPPORT,
        title=f"New support ticket {ticket.reference}",
        ticket=ticket,
        exclude=author,
    )
    return ticket


@transaction.atomic
def post_ticket_message(
    *, ticket: SupportTicket, author, body: str, internal: bool = False
) -> TicketMessage:
    staff = _staff_actor(author)
    internal = bool(internal)
    if staff:
        if not has_perm(author, SUPPORT_HANDLE):
            raise PermissionFailure("You cannot access that ticket.")
        if not internal and ticket.status == SupportTicketStatus.CLOSED:
            raise ValidationFailure("Reopen the ticket before replying to the client.")
    else:
        if not Membership.objects.filter(
            organization_id=ticket.organization_id, user=author
        ).exists():
            raise PermissionFailure("You cannot access that ticket.")
        if internal:
            raise PermissionFailure("Internal notes are only available to staff.")
        if ticket.status == SupportTicketStatus.CLOSED:
            raise ValidationFailure("This ticket is closed. Open a new one instead.")

    message = TicketMessage.objects.create(
        ticket=ticket,
        author=author,
        author_name=_actor_name(author),
        author_role=AuthorRole.STAFF if staff else AuthorRole.CLIENT,
        body=_clean_body(body),
        is_internal=internal,
    )

    ticket.last_message_at = timezone.now()
    update_fields = ["last_message_at", "updated_at"]
    if staff and not internal and ticket.status == SupportTicketStatus.OPEN:
        ticket.status = SupportTicketStatus.PENDING
        update_fields.append("status")
    if not staff and ticket.status in (
        SupportTicketStatus.PENDING,
        SupportTicketStatus.RESOLVED,
    ):
        ticket.status = SupportTicketStatus.OPEN
        ticket.resolved_at = None
        update_fields.extend(["status", "resolved_at"])
    ticket.save(update_fields=update_fields)

    audit_service.record(
        action="support.replied",
        actor=author,
        organization_id=ticket.organization_id,
        resource_type="support_ticket",
        resource_id=str(ticket.pk),
        metadata={"message_id": str(message.pk), "internal": internal},
    )

    if not internal:
        recipients = (
            _ticket_client_recipients(ticket)
            if staff
            else ([ticket.assigned_to] if ticket.assigned_to else _support_recipients())
        )
        notify(
            recipients,
            organization_id=ticket.organization_id,
            kind=NotificationKind.SUPPORT,
            title=(
                f"Support replied to {ticket.reference}"
                if staff
                else f"Client replied to {ticket.reference}"
            ),
            ticket=ticket,
            exclude=author,
        )
    return message


@transaction.atomic
def assign_ticket(ticket: SupportTicket, actor, assignee) -> SupportTicket:
    if not has_perm(actor, SUPPORT_HANDLE):
        raise PermissionFailure("You cannot assign tickets.")
    if assignee.staff_role not in (
        StaffRole.SUPPORT,
        StaffRole.OPERATIONS,
        StaffRole.ADMIN,
    ):
        raise ValidationFailure("Choose a support team member.")
    if not assignee.is_active:
        raise ValidationFailure("That account is inactive.")
    ticket.assigned_to = assignee
    ticket.save(update_fields=["assigned_to", "updated_at"])
    audit_service.record(
        action="support.assigned",
        actor=actor,
        organization_id=ticket.organization_id,
        resource_type="support_ticket",
        resource_id=str(ticket.pk),
        metadata={"assignee_id": str(assignee.pk)},
    )
    notify(
        [assignee],
        organization_id=ticket.organization_id,
        kind=NotificationKind.SUPPORT,
        title=f"Ticket {ticket.reference} assigned to you",
        ticket=ticket,
        exclude=actor,
    )
    return ticket


@transaction.atomic
def set_ticket_status(ticket: SupportTicket, actor, status: str) -> SupportTicket:
    if not has_perm(actor, SUPPORT_HANDLE):
        raise PermissionFailure("You cannot change ticket status.")
    if status not in SupportTicketStatus.values:
        raise ValidationFailure("Choose a valid ticket status.")
    previous = ticket.status
    ticket.status = status
    ticket.resolved_at = (
        timezone.now()
        if status in (SupportTicketStatus.RESOLVED, SupportTicketStatus.CLOSED)
        else None
    )
    ticket.save(update_fields=["status", "resolved_at", "updated_at"])
    audit_service.record(
        action="support.status_changed",
        actor=actor,
        organization_id=ticket.organization_id,
        resource_type="support_ticket",
        resource_id=str(ticket.pk),
        metadata={"from": previous, "to": status},
    )
    if status in (SupportTicketStatus.RESOLVED, SupportTicketStatus.CLOSED):
        notify(
            _ticket_client_recipients(ticket),
            organization_id=ticket.organization_id,
            kind=NotificationKind.SUPPORT,
            title=f"Ticket {ticket.reference} was {status}",
            ticket=ticket,
            exclude=actor,
        )
    return ticket


@transaction.atomic
def close_ticket_by_client(ticket: SupportTicket, actor) -> SupportTicket:
    if not Membership.objects.filter(
        organization_id=ticket.organization_id, user=actor
    ).exists():
        raise PermissionFailure("You cannot access that ticket.")
    if ticket.status == SupportTicketStatus.CLOSED:
        return ticket
    ticket.status = SupportTicketStatus.CLOSED
    ticket.resolved_at = timezone.now()
    ticket.save(update_fields=["status", "resolved_at", "updated_at"])
    audit_service.record(
        action="support.closed_by_client",
        actor=actor,
        organization_id=ticket.organization_id,
        resource_type="support_ticket",
        resource_id=str(ticket.pk),
    )
    return ticket


def task_thread(task, viewer):
    messages = task.messages.select_related("author")
    if not _staff_actor(viewer):
        messages = messages.filter(is_internal=False)
    return messages


def ticket_thread(ticket: SupportTicket, viewer):
    messages = ticket.messages.select_related("author")
    if not _staff_actor(viewer):
        messages = messages.filter(is_internal=False)
    return messages


def mark_notifications_read(user, *, ids=None) -> int:
    queryset = Notification.objects.filter(user=user, read_at__isnull=True)
    if ids:
        queryset = queryset.filter(pk__in=ids)
    return queryset.update(read_at=timezone.now())


def unread_count(user) -> int:
    if user is None or not getattr(user, "is_authenticated", False):
        return 0
    return Notification.objects.filter(user=user, read_at__isnull=True).count()
