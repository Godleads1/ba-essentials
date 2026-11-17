from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
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
    # US dollar list price in cents, for businesses outside Africa.
    price_usd_minor = models.BigIntegerField(default=0)
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


class BillingInterval(models.TextChoices):
    MONTH = "month", "Monthly"
    YEAR = "year", "Yearly"


class BillingSettings(models.Model):
    """Prices the super admin sets without a code change. One row only (pk=1)."""

    yearly_discount_pct = models.PositiveSmallIntegerField(
        default=15,
        validators=[MaxValueValidator(50)],
        help_text="Discount for paying yearly, as a percentage (0 to 50).",
    )
    extra_legal_fee_ngn_minor = models.BigIntegerField(
        default=2_500_000, help_text="Extra legal review beyond the plan, in kobo (₦25,000 = 2500000)."
    )
    extra_legal_fee_usd_minor = models.BigIntegerField(
        default=4_000, help_text="Extra legal review beyond the plan, in US cents ($40 = 4000)."
    )
    africa_surcharge_ngn_minor = models.BigIntegerField(
        default=10_000,
        help_text="Added to the naira price for the rest of Africa before converting, in kobo (₦100 = 10000).",
    )
    fallback_ngn_per_usd = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal(100)), MaxValueValidator(Decimal(20000))],
        help_text="Used only when the live exchange rate can't be fetched. Leave empty to pause dollar checkouts instead.",
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "billing settings"
        verbose_name_plural = "billing settings"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(yearly_discount_pct__lte=50), name="billing_discount_at_most_50"
            ),
            models.CheckConstraint(
                condition=models.Q(extra_legal_fee_ngn_minor__gte=0)
                & models.Q(extra_legal_fee_usd_minor__gte=0)
                & models.Q(africa_surcharge_ngn_minor__gte=0),
                name="billing_amounts_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return "Billing settings"

    @classmethod
    def current(cls) -> BillingSettings:
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    # Renewal unpaid after the grace period: service paused (Terms 5.6.4).
    PAST_DUE = "past_due", "Paused: renewal unpaid"
    CANCELLED = "cancelled", "Ended"


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
    interval = models.CharField(
        max_length=8, choices=BillingInterval.choices, default=BillingInterval.MONTH
    )
    # Cancelling stops the next renewal; access runs to period_end (Terms 5.5.3).
    cancel_at_period_end = models.BooleanField(default=False)
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    # A move to a smaller plan starts at the next renewal (Terms 5.7).
    next_plan = models.ForeignKey(
        "Plan", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # The period_end a renewal reminder was sent for, so it's sent once (Terms 5.5.4).
    reminder_sent_for = models.DateTimeField(null=True, blank=True)
    # Renewal payment problems (Terms 5.6): grace period, retries, pause.
    grace_until = models.DateTimeField(null=True, blank=True)
    renewal_attempts = models.PositiveSmallIntegerField(default=0)
    last_renewal_attempt_at = models.DateTimeField(null=True, blank=True)
    grace_last_day_reminded = models.BooleanField(default=False)
    paused_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.organization.name}: {self.plan.name}"

    @property
    def in_grace(self) -> bool:
        from django.utils import timezone

        return bool(self.grace_until and timezone.now() <= self.grace_until)


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
    # For plan invoices: how long the payment covers.
    interval = models.CharField(
        max_length=8, choices=BillingInterval.choices, default=BillingInterval.MONTH
    )
    # For a renewal invoice: the period_end it renews from. One per period.
    renews_period_end = models.DateTimeField(null=True, blank=True)
    # Naira per US dollar used to price a rest-of-Africa invoice, kept as a record.
    fx_ngn_per_usd = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
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



class RenewalConsent(models.Model):
    """The client's agreement to automatic renewal, as Terms 5.5.3 requires us to record it:
    the wording shown, the terms version, when, and the provider reference for the saved card."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="renewal_consents"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT, related_name="renewal_consent")
    wording = models.TextField()
    terms_version = models.CharField(max_length=32)
    agreed_at = models.DateTimeField(auto_now_add=True)
    provider_reference = models.CharField(max_length=64, blank=True)

    def __str__(self) -> str:
        return f"Renewal consent {self.invoice.number}"


class PaymentMethod(TimeStampedModel):
    """A card saved with the payment provider for renewals. We hold only the provider's
    token (encrypted) and what the client needs to recognise the card, never the card number."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="payment_methods"
    )
    provider = models.CharField(max_length=32)
    token_encrypted = models.TextField()
    brand = models.CharField(max_length=32, blank=True)
    last4 = models.CharField(max_length=4, blank=True)
    exp_month = models.PositiveSmallIntegerField(null=True, blank=True)
    exp_year = models.PositiveSmallIntegerField(null=True, blank=True)
    bank = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)  # the provider ties the token to this email
    consent = models.ForeignKey(RenewalConsent, on_delete=models.PROTECT, related_name="+")
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "removed_at"])]

    def __str__(self) -> str:
        return f"{self.brand or 'Card'} ending {self.last4}"

    @property
    def label(self) -> str:
        expiry = f", expires {self.exp_month:02d}/{str(self.exp_year)[-2:]}" if self.exp_month and self.exp_year else ""
        return f"{self.brand or 'Card'} ending {self.last4}{expiry}"
