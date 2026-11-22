from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class MenuPlacement(models.TextChoices):
    FOOTER = "footer", "Footer only"
    HEADER = "header", "Header and footer"
    NONE = "none", "Not in the menu (link to it yourself)"


class Page(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=120, unique=True)
    # Where the page is linked from once it is published. Changing it changes the
    # live site, so it needs the publish permission (apps/website/services.py).
    menu = models.CharField(max_length=8, choices=MenuPlacement.choices, default=MenuPlacement.FOOTER)
    menu_order = models.PositiveSmallIntegerField(default=100, help_text="Lower numbers come first.")
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


class PageImage(models.Model):
    """An image for website pages. Stored re-encoded (no metadata), shown only once a published page uses it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    storage_key = models.CharField(max_length=200, unique=True)
    content_type = models.CharField(max_length=32)
    alt_text = models.CharField(max_length=200)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size_bytes = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.alt_text

    @property
    def snippet(self) -> str:
        """What an editor pastes into a page body."""
        return f"![{self.alt_text}](image:{self.pk})"


class PageRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="revisions")
    revision_number = models.PositiveIntegerField()
    title = models.CharField(max_length=160)
    summary = models.CharField(max_length=255, blank=True)
    body = models.TextField()
    # Search engines and link previews. Empty description falls back to the summary.
    meta_description = models.CharField(max_length=160, blank=True)
    hide_from_search = models.BooleanField(default=False)
    share_image = models.ForeignKey(
        PageImage, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Shown when the page is shared on WhatsApp, LinkedIn and similar.",
    )
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


class HomeRevision(models.Model):
    """One saved version of the home page words (apps/website/home.py)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.PositiveIntegerField(unique=True)
    content = models.JSONField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]

    def __str__(self) -> str:
        return f"Home page v{self.number}"


class HomePage(TimeStampedModel):
    """A single row: which version of the home page words is live (none = the original wording)."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    published_revision = models.ForeignKey(
        HomeRevision, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
