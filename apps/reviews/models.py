from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Deliverable(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.OneToOneField(
        "tasks.Task", on_delete=models.CASCADE, related_name="deliverable"
    )

    def __str__(self) -> str:
        return f"Deliverable for {self.task.reference}"


class DeliverableVersionStatus(models.TextChoices):
    PENDING_REVIEW = "pending_review", "Pending review"
    APPROVED = "approved", "Approved"
    REWORK_REQUIRED = "rework_required", "Rework required"
    RELEASED = "released", "Released"
    SUPERSEDED = "superseded", "Superseded"


class DeliverableVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deliverable = models.ForeignKey(
        Deliverable, on_delete=models.CASCADE, related_name="versions"
    )
    version_number = models.PositiveIntegerField()
    file = models.OneToOneField(
        "files.PrivateFile", on_delete=models.PROTECT, related_name="deliverable_version"
    )
    notes = models.TextField(blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    status = models.CharField(
        max_length=20,
        choices=DeliverableVersionStatus.choices,
        default=DeliverableVersionStatus.PENDING_REVIEW,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["deliverable", "version_number"],
                name="uniq_deliverable_version_number",
            )
        ]

    def __str__(self) -> str:
        return f"{self.deliverable.task.reference} v{self.version_number}"


class ReviewDecision(models.TextChoices):
    APPROVE = "approve", "Approve"
    REWORK = "rework", "Request rework"


class Review(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deliverable_version = models.ForeignKey(
        DeliverableVersion, on_delete=models.CASCADE, related_name="reviews"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reviews"
    )
    decision = models.CharField(max_length=16, choices=ReviewDecision.choices)
    findings = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ClientDecision(models.TextChoices):
    ACCEPT = "accept", "Accept"
    REVISION = "revision", "Request revision"


class ClientAcceptance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        "tasks.Task", on_delete=models.CASCADE, related_name="client_acceptances"
    )
    deliverable_version = models.ForeignKey(
        DeliverableVersion, on_delete=models.PROTECT, related_name="client_decisions"
    )
    decision = models.CharField(max_length=16, choices=ClientDecision.choices)
    note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
