from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class FilePurpose(models.TextChoices):
    ATTACHMENT = "attachment", "Request attachment"
    DELIVERABLE = "deliverable", "Deliverable"


class ScanStatus(models.TextChoices):
    PENDING = "pending", "Scan pending"
    CLEAN = "clean", "Clean"
    QUARANTINED = "quarantined", "Quarantined"
    FAILED = "failed", "Scan failed"


class PrivateFile(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="files"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="uploaded_files",
    )
    purpose = models.CharField(max_length=16, choices=FilePurpose.choices)
    storage_key = models.CharField(max_length=255, unique=True)
    original_name = models.CharField(max_length=255)
    size_bytes = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    scan_status = models.CharField(
        max_length=16, choices=ScanStatus.choices, default=ScanStatus.PENDING
    )
    scan_detail = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self) -> str:
        return self.original_name

    @property
    def is_downloadable(self) -> bool:
        return self.scan_status == ScanStatus.CLEAN
