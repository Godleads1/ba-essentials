from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class QuoteStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    SUPERSEDED = "superseded", "Superseded"
    EXPIRED = "expired", "Expired"


class Quote(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.OneToOneField(
        "tasks.Task", on_delete=models.CASCADE, related_name="quote"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    def __str__(self) -> str:
        return f"Quote for {self.task.reference}"


class QuoteVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    scope_summary = models.TextField()
    exclusions = models.TextField(blank=True)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="NGN")
    turnaround_days = models.PositiveSmallIntegerField()
    revision_allowance = models.PositiveSmallIntegerField(default=1)
    payment_terms = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=QuoteStatus.choices, default=QuoteStatus.DRAFT
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["quote", "version_number"], name="uniq_quote_version_number"
            ),
            models.UniqueConstraint(
                fields=["quote"],
                condition=models.Q(status="sent"),
                name="uniq_sent_quote_version",
            ),
        ]

    def __str__(self) -> str:
        return f"v{self.version_number} ({self.get_status_display()})"

    @property
    def amount_display(self) -> str:
        return f"{self.currency} {self.amount_minor / 100:,.2f}"


class QuoteAcceptance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote_version = models.OneToOneField(
        QuoteVersion, on_delete=models.PROTECT, related_name="acceptance"
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="quote_acceptances"
    )
    accepted_at = models.DateTimeField(auto_now_add=True)
    terms_fingerprint = models.CharField(max_length=64)
