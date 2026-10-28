from __future__ import annotations

from django.contrib import admin

from .models import (
    Invoice,
    PaymentAttempt,
    Plan,
    ProviderWebhookEvent,
    Subscription,
    UsageLedger,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "monthly_task_allowance", "price_minor", "is_active")
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
