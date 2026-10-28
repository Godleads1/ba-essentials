from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimeStampedModel


class ServiceCategory(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    summary = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "service categories"

    def __str__(self) -> str:
        return self.name


class ServiceDefinition(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(
        ServiceCategory, on_delete=models.PROTECT, related_name="services"
    )
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category__sort_order", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def current_version(self):
        return self.versions.filter(is_current=True).first()


class ServiceState(models.TextChoices):
    AVAILABLE = "available", "Available"
    SCOPING_ONLY = "scoping_only", "Scoping only"
    UNAVAILABLE = "unavailable", "Unavailable"


class ServiceVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definition = models.ForeignKey(
        ServiceDefinition, on_delete=models.CASCADE, related_name="versions"
    )
    version_number = models.PositiveIntegerField()
    scope_summary = models.TextField()
    deliverables_summary = models.TextField()
    turnaround_days = models.PositiveSmallIntegerField(default=7)
    revision_allowance = models.PositiveSmallIntegerField(default=1)
    requires_quote = models.BooleanField(
        default=True, help_text="Scope and price are quoted before work starts."
    )
    state = models.CharField(
        max_length=16, choices=ServiceState.choices, default=ServiceState.SCOPING_ONLY
    )
    is_current = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["definition", "-version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["definition", "version_number"],
                name="uniq_service_version_number",
            ),
            models.UniqueConstraint(
                fields=["definition"],
                condition=models.Q(is_current=True),
                name="uniq_current_service_version",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.definition.name} v{self.version_number}"

    @property
    def is_available(self) -> bool:
        return self.state == ServiceState.AVAILABLE and self.definition.is_active

    def clean(self):
        if self.turnaround_days < 1:
            raise ValidationError({"turnaround_days": "Turnaround must be at least one day."})
