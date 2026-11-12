from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class Page(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=120, unique=True)
    published_revision = models.ForeignKey(
        "website.PageRevision",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self) -> str:
        return self.slug

    @property
    def is_published(self) -> bool:
        return self.published_revision_id is not None


class PageRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="revisions")
    revision_number = models.PositiveIntegerField()
    title = models.CharField(max_length=160)
    summary = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-revision_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["page", "revision_number"],
                name="uniq_page_revision_number",
            )
        ]

    def __str__(self) -> str:
        return f"{self.page.slug} v{self.revision_number}"


class ContactStatus(models.TextChoices):
    NEW = "new", "New"
    HANDLED = "handled", "Handled"
    SPAM = "spam", "Spam"


class ContactMessage(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    company = models.CharField(max_length=150, blank=True)
    message = models.TextField()
    status = models.CharField(
        max_length=16, choices=ContactStatus.choices, default=ContactStatus.NEW, db_index=True
    )
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    source_ip_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Enquiry from {self.name}"


class Testimonial(TimeStampedModel):
    """A real client quote shown on the public landing page.

    Only add quotes a client actually gave and agreed to have published.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote = models.TextField(max_length=400)
    name = models.CharField(max_length=120)
    role = models.CharField(
        max_length=160, blank=True, help_text="Job title and business, e.g. Founder, Acme Foods."
    )
    rating = models.PositiveSmallIntegerField(
        default=5, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    consent_confirmed = models.BooleanField(
        default=False,
        help_text="The client has agreed to this quote, name and role being published.",
    )
    is_published = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "-created_at"]

    def __str__(self) -> str:
        return f"{self.name}: {self.quote[:40]}"

    def clean(self):
        if self.is_published and not self.consent_confirmed:
            raise ValidationError(
                {"is_published": "Confirm the client's consent before publishing."}
            )

    @property
    def initials(self) -> str:
        parts = self.name.split()
        return "".join(part[0] for part in (parts[:1] + parts[-1:] if len(parts) > 1 else parts)).upper()

    @property
    def stars(self) -> range:
        return range(self.rating)
