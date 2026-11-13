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
    # Higher rank = bigger plan. A plan includes every service category whose
    # minimum rank is at or below its own (Starter 1, Growth 2, Dedicated 3).
    rank = models.PositiveSmallIntegerField(default=0)
    # Legal & IP reviews included per window, e.g. 1 every 3 months on Starter.
    legal_review_allowance = models.PositiveSmallIntegerField(default=0)
    legal_review_period_months = models.PositiveSmallIntegerField(
        default=1, choices=[(1, "Every month"), (3, "Every quarter")]
    )
    price_minor = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="NGN")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["price_minor"]

    def __str__(self) -> str:
        return self.name

    @property
    def legal_review_label(self) -> str:
        n = self.legal_review_allowance
        per = "quarter" if self.legal_review_period_months == 3 else "month"
        return f"{n} legal review{'' if n == 1 else 's'} a {per}"


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
    LEGAL = "legal", "Legal review allowance"


COVERING_USAGE_KINDS = (UsageKind.ALLOWANCE, UsageKind.LEGAL)


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
    # Set for a plan's monthly charge; empty for a quoted request.
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoices",
    )
    # Set for a mid-cycle upgrade: the plan being left. The invoice then charges
    # only the difference for the rest of the current period.
    upgrade_from = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    # The end of the period the upgrade was priced for. It applies only within it.
    upgrade_period_end = models.DateTimeField(null=True, blank=True)
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
    # Paystack took the money but the invoice was already paid or withdrawn.
    REFUND_DUE = "refund_due", "Refund due"


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


class FeeRequestStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_APPROVAL = "pending_approval", "Awaiting second approval"
    SENT = "sent", "Sent to client"
    PAID = "paid", "Paid by client"
    SETTLED = "settled", "Paid to organisations"
    VOID = "void", "Withdrawn"


# Statuses the client can see. Drafts and unapproved requests stay internal.
CLIENT_VISIBLE_FEE_STATUSES = (
    FeeRequestStatus.SENT,
    FeeRequestStatus.PAID,
    FeeRequestStatus.SETTLED,
)


class FeeRequest(TimeStampedModel):
    """Third-party fees (registry, government, institution) found during scoping.

    banda collects them at cost, plus Paystack's charge so the full amount
    reaches each organisation, and pays them on the client's behalf (Terms 5.3).
    Nothing is filed or booked until the client has paid.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        "tasks.Task", on_delete=models.PROTECT, related_name="fee_requests"
    )
    status = models.CharField(
        max_length=20, choices=FeeRequestStatus.choices, default=FeeRequestStatus.DRAFT
    )
    currency = models.CharField(max_length=3, default="NGN")
    # Fixed when a second staff member approves, so the client pays exactly what was approved.
    net_minor = models.BigIntegerField(default=0)
    processing_minor = models.BigIntegerField(default=0)
    total_minor = models.BigIntegerField(default=0)
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    invoice = models.OneToOneField(
        Invoice,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fee_request",
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    # What Paystack actually kept, from its own verification. A foreign card can
    # cost more than the local rate we added; the gap is recorded for finance.
    provider_fee_minor = models.BigIntegerField(null=True, blank=True)
    shortfall_minor = models.BigIntegerField(default=0)
    card_country = models.CharField(max_length=2, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["task", "status"])]

    def __str__(self) -> str:
        return f"Fees for {self.task.reference} ({self.get_status_display()})"

    @property
    def is_client_visible(self) -> bool:
        return self.status in CLIENT_VISIBLE_FEE_STATUSES

    @property
    def is_open(self) -> bool:
        return self.status in (
            FeeRequestStatus.DRAFT, FeeRequestStatus.PENDING_APPROVAL, FeeRequestStatus.SENT
        )


class FeeLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fee_request = models.ForeignKey(
        FeeRequest, on_delete=models.CASCADE, related_name="lines"
    )
    payee = models.CharField(max_length=160)
    description = models.CharField(max_length=255)
    amount_minor = models.BigIntegerField()
    # Proof of the official amount (fee schedule, Remita invoice, etc.).
    evidence = models.OneToOneField(
        "files.PrivateFile", on_delete=models.PROTECT, related_name="fee_evidence_for"
    )
    receipt = models.OneToOneField(
        "files.PrivateFile",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fee_receipt_for",
    )
    payee_reference = models.CharField(max_length=64, blank=True)
    paid_to_payee_at = models.DateTimeField(null=True, blank=True)
    paid_to_payee_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_minor__gt=0), name="fee_line_amount_positive"
            )
        ]

    def __str__(self) -> str:
        return f"{self.payee}: {self.amount_minor}"
