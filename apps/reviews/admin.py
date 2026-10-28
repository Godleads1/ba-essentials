from __future__ import annotations

from django.contrib import admin

from .models import (
    ClientAcceptance,
    Deliverable,
    DeliverableVersion,
    Review,
)


class DeliverableVersionInline(admin.TabularInline):
    model = DeliverableVersion
    extra = 0
    can_delete = False
    readonly_fields = (
        "version_number",
        "file",
        "status",
        "submitted_by",
        "reviewed_by",
        "released_at",
        "created_at",
    )


@admin.register(Deliverable)
class DeliverableAdmin(admin.ModelAdmin):
    list_display = ("task", "created_at")
    search_fields = ("task__reference",)
    inlines = (DeliverableVersionInline,)


@admin.register(DeliverableVersion)
class DeliverableVersionAdmin(admin.ModelAdmin):
    list_display = ("deliverable", "version_number", "status", "submitted_by", "created_at")
    list_filter = ("status",)
    search_fields = ("deliverable__task__reference",)
    readonly_fields = (
        "deliverable",
        "version_number",
        "file",
        "notes",
        "submitted_by",
        "status",
        "reviewed_by",
        "reviewed_at",
        "review_note",
        "released_at",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("deliverable_version", "reviewer", "decision", "created_at")
    list_filter = ("decision",)
    search_fields = ("deliverable_version__deliverable__task__reference",)
    readonly_fields = (
        "deliverable_version",
        "reviewer",
        "decision",
        "findings",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ClientAcceptance)
class ClientAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("task", "deliverable_version", "decision", "decided_by", "created_at")
    list_filter = ("decision",)
    search_fields = ("task__reference",)
    readonly_fields = (
        "task",
        "deliverable_version",
        "decision",
        "note",
        "decided_by",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
