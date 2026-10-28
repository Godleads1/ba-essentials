from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class Counter(models.Model):
    """Monotonic counter used to build human-readable references."""

    key = models.CharField(max_length=32, primary_key=True)
    value = models.PositiveIntegerField(default=0)


class IdempotencyKey(models.Model):
    """Records the outcome of a non-repeatable action for a given client key."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="idempotency_keys"
    )
    scope = models.CharField(max_length=64)
    key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    response_ref = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "scope", "key"], name="uniq_idempotency_user_scope_key"
            )
        ]
        indexes = [models.Index(fields=["scope", "key"])]


class OutboxEvent(models.Model):
    """Durable record of a side effect that must follow a committed transaction."""

    class Topic(models.TextChoices):
        EMAIL = "email", "Email"
        NOTIFICATION = "notification", "Notification"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.CharField(max_length=32, choices=Topic.choices)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["processed_at", "created_at"])]
