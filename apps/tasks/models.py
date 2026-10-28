from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class TaskStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    TRIAGE = "triage", "Triage"
    NEEDS_INFORMATION = "needs_information", "Needs information"
    SCOPING = "scoping", "Scoping"
    AWAITING_CLIENT_APPROVAL = "awaiting_client_approval", "Awaiting client approval"
    AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
    READY_FOR_ASSIGNMENT = "ready_for_assignment", "Ready for assignment"
    ASSIGNED = "assigned", "Assigned"
    IN_PROGRESS = "in_progress", "In progress"
    INTERNAL_REVIEW = "internal_review", "Internal review"
    REWORK_REQUIRED = "rework_required", "Rework required"
    APPROVED_FOR_RELEASE = "approved_for_release", "Approved for release"
    DELIVERED = "delivered", "Delivered"
    REVISION_REQUESTED = "revision_requested", "Revision requested"
    COMPLETED = "completed", "Completed"
    ON_HOLD = "on_hold", "On hold"
    CANCELLED = "cancelled", "Cancelled"


OPEN_STATUSES = (
    TaskStatus.SUBMITTED,
    TaskStatus.TRIAGE,
    TaskStatus.NEEDS_INFORMATION,
    TaskStatus.SCOPING,
    TaskStatus.AWAITING_CLIENT_APPROVAL,
    TaskStatus.AWAITING_PAYMENT,
    TaskStatus.READY_FOR_ASSIGNMENT,
    TaskStatus.ASSIGNED,
    TaskStatus.IN_PROGRESS,
    TaskStatus.INTERNAL_REVIEW,
    TaskStatus.REWORK_REQUIRED,
    TaskStatus.APPROVED_FOR_RELEASE,
    TaskStatus.DELIVERED,
    TaskStatus.REVISION_REQUESTED,
)

TERMINAL_STATUSES = (TaskStatus.COMPLETED, TaskStatus.CANCELLED)

# Statuses where a hold may be applied.
HOLDABLE_STATUSES = tuple(
    status for status in OPEN_STATUSES if status != TaskStatus.DELIVERED
)


class Priority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class Task(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=24, unique=True)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="tasks"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_tasks",
    )
    service_version = models.ForeignKey(
        "catalogue.ServiceVersion", on_delete=models.PROTECT, related_name="tasks"
    )
    title = models.CharField(max_length=160)
    brief = models.TextField()
    desired_outcome = models.TextField(blank=True)
    priority = models.CharField(
        max_length=16, choices=Priority.choices, default=Priority.MEDIUM
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=32, choices=TaskStatus.choices, default=TaskStatus.DRAFT, db_index=True
    )
    held_from_status = models.CharField(
        max_length=32, choices=TaskStatus.choices, blank=True
    )
    needs_info_question = models.TextField(blank=True)
    client_information = models.TextField(blank=True)
    revision_allowance_remaining = models.PositiveSmallIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "status", "-created_at"]),
            models.Index(fields=["status", "-updated_at"]),
        ]

    def __str__(self) -> str:
        return self.reference

    @property
    def is_open(self) -> bool:
        return self.status not in TERMINAL_STATUSES

    @property
    def active_assignment(self):
        return self.assignments.filter(status=AssignmentStatus.ACTIVE).first()

    @property
    def latest_deliverable_version(self):
        deliverable = getattr(self, "deliverable", None)
        if deliverable is None:
            return None
        return deliverable.versions.order_by("-version_number").first()


class TaskStatusEvent(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="status_events")
    from_status = models.CharField(max_length=32, choices=TaskStatus.choices)
    to_status = models.CharField(max_length=32, choices=TaskStatus.choices)
    action = models.CharField(max_length=40)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    actor_role = models.CharField(max_length=40, blank=True)
    reason = models.TextField(blank=True)
    correlation_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["task", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.task.reference}: {self.from_status} -> {self.to_status}"


class TaskAttachment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    file = models.OneToOneField(
        "files.PrivateFile", on_delete=models.CASCADE, related_name="task_attachment"
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class AssignmentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class Assignment(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="assignments")
    specialist = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    status = models.CharField(
        max_length=16, choices=AssignmentStatus.choices, default=AssignmentStatus.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["task"],
                condition=models.Q(status="active"),
                name="uniq_active_assignment_per_task",
            )
        ]
        indexes = [models.Index(fields=["specialist", "status"])]

    def __str__(self) -> str:
        return f"{self.task.reference} -> {self.specialist.email}"


class WorkLog(models.Model):
    assignment = models.ForeignKey(
        Assignment, on_delete=models.CASCADE, related_name="work_logs"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    note = models.TextField()
    minutes = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
