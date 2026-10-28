from __future__ import annotations

from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "organization", "resource_type", "result")
    list_filter = ("result", "action")
    search_fields = ("actor__email", "organization__name", "resource_id", "correlation_id")
    date_hierarchy = "created_at"
    readonly_fields = (
        "actor",
        "actor_type",
        "organization",
        "action",
        "resource_type",
        "resource_id",
        "result",
        "reason",
        "correlation_id",
        "metadata",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
