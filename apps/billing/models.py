from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Plan(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    monthly_task_allowance = models.PositiveSmallIntegerField(default=0)
    price_minor = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="NGN")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["price_minor"]

    def __str__(self) -> str:
        return self.name


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAST_DUE = "past_due", "Past due"
    CANCELLED = "cancelled", "Cancelled"


class Subscription(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        "organizations.Organization", on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    status = models.CharField(
        max_length=16, choices=SubscriptionStatus.choices, default=SubscriptionStatus.ACTIVE
    )

    def __str__(self) -> str:
        return f"{self.organization.name}: {self.plan.name}"


class UsageKind(models.TextChoices):
    ALLOWANCE = "allowance", "Plan allowance"


class UsageLedger(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="usage_entries"
    )
    task = models.ForeignKey(
        "tasks.Task", on_delete=models.PROTECT, related_name="usage_entries"
    )
    kind = models.CharField(
        max_length=16, choices=UsageKind.choices, default=UsageKind.ALLOWANCE
    )
    units = models.PositiveSmallIntegerField(default=1)
    period_start = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["task", "kind"], name="uniq_usage_per_task_kind"
            )
        ]
        indexes = [models.Index(fields=["organization", "period_start"])]


class InvoiceStatus(models.TextChoices):
    ISSUED = "issued", "Issued"
    PAID = "paid", "Paid"
    VOID = "void", "Void"


class Invoice(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=32, unique=True)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="invoices"
    )
    task = models.ForeignKey(
        "tasks.Task",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoices",
    )
    quote_version = models.ForeignKey(
        "quotes.QuoteVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoices",
    )
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="NGN")
    status = models.CharField(
        max_length=16, choices=InvoiceStatus.choices, default=InvoiceStatus.ISSUED
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["-issued_at"]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self) -> str:
        return f"{self.number} ({self.get_status_display()})"

    @property
    def amount_display(self) -> str:
        return f"{self.currency} {self.amount_minor / 100:,.2f}"


class PaymentStatus(models.TextChoices):
    INITIATED = "initiated", "Initiated"
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class PaymentAttempt(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, related_name="payment_attempts"
    )
    provider = models.CharField(max_length=32, default="paystack")
    reference = models.CharField(max_length=64, unique=True)
    amount_minor = models.BigIntegerField()
    currency = models.CharField(max_length=3, default="NGN")
    status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.INITIATED
    )
    provider_status = models.CharField(max_length=64, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.reference} ({self.get_status_display()})"


class ProviderWebhookEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=32, default="paystack")
    event_id = models.CharField(max_length=128)
    event_type = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    signature_valid = models.BooleanField(default=False)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "event_id"], name="uniq_provider_event"
            )
        ]
        indexes = [models.Index(fields=["received_at"])]
