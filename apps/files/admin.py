from __future__ import annotations

from django.contrib import admin

from .models import PrivateFile


@admin.register(PrivateFile)
class PrivateFileAdmin(admin.ModelAdmin):
    list_display = ("original_name", "organization", "purpose", "scan_status", "created_at")
    list_filter = ("purpose", "scan_status")
    search_fields = ("original_name", "organization__name", "sha256")
    readonly_fields = (
        "organization",
        "uploaded_by",
        "purpose",
        "storage_key",
        "original_name",
        "size_bytes",
        "content_type",
        "sha256",
        "scan_status",
        "scan_detail",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
