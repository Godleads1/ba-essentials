from __future__ import annotations

from django.contrib import admin

from .models import Quote, QuoteAcceptance, QuoteVersion


class QuoteVersionInline(admin.TabularInline):
    model = QuoteVersion
    extra = 0
    can_delete = False
    readonly_fields = (
        "version_number",
        "scope_summary",
        "amount_minor",
        "currency",
        "turnaround_days",
        "revision_allowance",
        "status",
        "sent_at",
        "expires_at",
        "created_at",
    )


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ("task", "created_by", "created_at")
    search_fields = ("task__reference",)
    inlines = (QuoteVersionInline,)


@admin.register(QuoteVersion)
class QuoteVersionAdmin(admin.ModelAdmin):
    list_display = ("quote", "version_number", "amount_minor", "status", "expires_at")
    list_filter = ("status", "currency")
    search_fields = ("quote__task__reference",)
    readonly_fields = (
        "quote",
        "version_number",
        "scope_summary",
        "exclusions",
        "amount_minor",
        "currency",
        "turnaround_days",
        "revision_allowance",
        "payment_terms",
        "status",
        "expires_at",
        "created_by",
        "created_at",
        "sent_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QuoteAcceptance)
class QuoteAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("quote_version", "accepted_by", "accepted_at")
    readonly_fields = ("quote_version", "accepted_by", "accepted_at", "terms_fingerprint")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
