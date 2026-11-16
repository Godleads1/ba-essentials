from __future__ import annotations

from django.contrib import admin

from apps.audit import services as audit_service

from .models import (
    BillingSettings,
    Invoice,
    PaymentAttempt,
    Plan,
    ProviderWebhookEvent,
    Subscription,
    UsageLedger,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "name", "slug", "rank", "monthly_task_allowance", "legal_review_allowance",
        "legal_review_period_months", "price_minor", "price_usd_minor", "is_active",
    )
    list_filter = ("is_active", "currency")
    search_fields = ("name", "slug")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("organization", "plan", "status", "period_start", "period_end")
    list_filter = ("status", "plan")
    search_fields = ("organization__name",)


@admin.register(UsageLedger)
class UsageLedgerAdmin(admin.ModelAdmin):
    list_display = ("organization", "task", "kind", "units", "period_start", "created_at")
    list_filter = ("kind",)
    search_fields = ("organization__name", "task__reference")
    readonly_fields = (
        "organization",
        "task",
        "kind",
        "units",
        "period_start",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "organization", "amount_minor", "currency", "status", "issued_at")
    list_filter = ("status", "currency")
    search_fields = ("number", "organization__name", "task__reference")
    readonly_fields = (
        "number",
        "organization",
        "task",
        "quote_version",
        "amount_minor",
        "currency",
        "status",
        "issued_at",
        "due_at",
        "paid_at",
        "verified_by",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ("reference", "invoice", "provider", "amount_minor", "status", "verified_at")
    list_filter = ("provider", "status")
    search_fields = ("reference", "invoice__number")
    readonly_fields = (
        "invoice",
        "provider",
        "reference",
        "amount_minor",
        "currency",
        "status",
        "provider_status",
        "verified_by",
        "verified_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ProviderWebhookEvent)
class ProviderWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("provider", "event_id", "event_type", "outcome", "received_at")
    list_filter = ("provider", "event_type")
    search_fields = ("event_id",)
    readonly_fields = (
        "provider",
        "event_id",
        "event_type",
        "payload_hash",
        "payload",
        "signature_valid",
        "received_at",
        "processed_at",
        "outcome",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BillingSettings)
class BillingSettingsAdmin(admin.ModelAdmin):
    """Yearly discount, extra legal review fees and the rest-of-Africa amount.

    Only super admins can see or change these, there is only ever one row, and
    every change is written to the audit log with the old and new values.
    """

    fields = (
        "yearly_discount_pct",
        "extra_legal_fee_ngn_minor",
        "extra_legal_fee_usd_minor",
        "africa_surcharge_ngn_minor",
        "fallback_ngn_per_usd",
        "updated_at",
        "updated_by",
    )
    readonly_fields = ("updated_at", "updated_by")

    def _super(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_module_permission(self, request):
        return self._super(request)

    def has_view_permission(self, request, obj=None):
        return self._super(request)

    def has_change_permission(self, request, obj=None):
        return self._super(request)

    def has_add_permission(self, request):
        return self._super(request) and not BillingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        before = {}
        if change:
            old = BillingSettings.objects.get(pk=obj.pk)
            before = {f: str(getattr(old, f)) for f in form.changed_data}
        obj.pk = 1
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        from django.core.cache import cache

        from .pricing import RATE_CACHE_KEY

        cache.delete(RATE_CACHE_KEY)
        audit_service.record(
            action="billing.settings_changed",
            actor=request.user,
            resource_type="billing_settings",
            resource_id="1",
            # One "old → new" entry per changed field (the audit log keeps flat values only).
            metadata={f: f"{before.get(f, '')} → {getattr(obj, f)}" for f in form.changed_data},
        )
