from __future__ import annotations

from django.contrib import admin

from .models import ServiceCategory, ServiceDefinition, ServiceVersion


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")


@admin.register(ServiceDefinition)
class ServiceDefinitionAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "is_active")
    list_filter = ("is_active", "category")
    search_fields = ("name", "slug")


@admin.register(ServiceVersion)
class ServiceVersionAdmin(admin.ModelAdmin):
    """Published versions are immutable; use the catalogue workflow to change state."""

    list_display = ("definition", "version_number", "state", "is_current", "created_at")
    list_filter = ("state", "is_current")
    search_fields = ("definition__name",)
    readonly_fields = (
        "definition",
        "version_number",
        "scope_summary",
        "deliverables_summary",
        "turnaround_days",
        "revision_allowance",
        "requires_quote",
        "state",
        "is_current",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
