from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class AuthorRole(models.TextChoices):
    CLIENT = "client", "Client"
    STAFF = "staff", "B & A team"


class TaskMessage(models.Model):
    """A message on a request thread.

    ``organization`` is denormalised from the task so tenant-scoped queries do
    not have to join through the task table.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        "tasks.Task", on_delete=models.CASCADE, related_name="messages"
    )
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="task_messages"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="task_messages",
    )
    author_name = models.CharField(max_length=150, blank=True)
    author_role = models.CharField(max_length=16, choices=AuthorRole.choices)
    body = models.TextField()
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["task", "created_at"]),
            models.Index(fields=["organization", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.task_id}: {self.author_role} message"


class SupportTicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    PENDING = "pending", "Awaiting client"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class SupportPriority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class SupportTicket(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=16, unique=True)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="support_tickets"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="support_tickets",
    )
    subject = models.CharField(max_length=160)
    status = models.CharField(
        max_length=16,
        choices=SupportTicketStatus.choices,
        default=SupportTicketStatus.OPEN,
        db_index=True,
    )
    priority = models.CharField(
        max_length=16,
        choices=SupportPriority.choices,
        default=SupportPriority.MEDIUM,
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tickets",
    )
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_message_at"]
        indexes = [
            models.Index(fields=["organization", "status", "-last_message_at"]),
            models.Index(fields=["assigned_to", "status"]),
        ]

    def __str__(self) -> str:
        return self.reference

    @property
    def is_open(self) -> bool:
        return self.status in (SupportTicketStatus.OPEN, SupportTicketStatus.PENDING)


class TicketMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="messages"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="ticket_messages",
    )
    author_name = models.CharField(max_length=150, blank=True)
    author_role = models.CharField(max_length=16, choices=AuthorRole.choices)
    body = models.TextField()
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["ticket", "created_at"])]

    def __str__(self) -> str:
        return f"{self.ticket_id}: {self.author_role} message"


class NotificationKind(models.TextChoices):
    TASK_MESSAGE = "task_message", "Request message"
    SUPPORT = "support", "Support"


class Notification(models.Model):
    """In-app notification. Titles name a reference, never message content."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    kind = models.CharField(max_length=32, choices=NotificationKind.choices)
    title = models.CharField(max_length=160)
    task = models.ForeignKey(
        "tasks.Task", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "read_at", "-created_at"])]

    def __str__(self) -> str:
        return self.title

    @property
    def is_read(self) -> bool:
        return self.read_at is not None
