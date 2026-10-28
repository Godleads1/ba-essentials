from __future__ import annotations

from django.contrib import admin

from .models import Assignment, Task, TaskAttachment, TaskStatusEvent, WorkLog


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("reference", "organization", "title", "status", "priority", "created_at")
    list_filter = ("status", "priority")
    search_fields = ("reference", "title", "organization__name")
    readonly_fields = ("reference", "status", "created_at", "updated_at", "completed_at")
    date_hierarchy = "created_at"


@admin.register(TaskStatusEvent)
class TaskStatusEventAdmin(admin.ModelAdmin):
    list_display = ("task", "from_status", "to_status", "action", "actor", "created_at")
    list_filter = ("action", "actor_role")
    search_fields = ("task__reference",)
    readonly_fields = (
        "task",
        "from_status",
        "to_status",
        "action",
        "actor",
        "actor_role",
        "reason",
        "correlation_id",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(admin.ModelAdmin):
    list_display = ("task", "file", "added_by", "created_at")
    search_fields = ("task__reference", "file__original_name")
    readonly_fields = ("created_at",)


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("task", "specialist", "status", "created_at", "accepted_at")
    list_filter = ("status",)
    search_fields = ("task__reference", "specialist__email")


@admin.register(WorkLog)
class WorkLogAdmin(admin.ModelAdmin):
    list_display = ("assignment", "author", "minutes", "created_at")
    search_fields = ("assignment__task__reference", "author__email")
    readonly_fields = ("created_at",)
