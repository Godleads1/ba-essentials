from django.contrib import admin

from .models import Notification, SupportTicket, TaskMessage, TicketMessage


@admin.register(TaskMessage)
class TaskMessageAdmin(admin.ModelAdmin):
    list_display = ("task", "author_role", "is_internal", "created_at")
    list_filter = ("author_role", "is_internal")
    search_fields = ("task__reference", "body")
    readonly_fields = ("created_at",)


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("reference", "organization", "status", "priority", "assigned_to", "last_message_at")
    list_filter = ("status", "priority")
    search_fields = ("reference", "subject", "organization__name")


@admin.register(TicketMessage)
class TicketMessageAdmin(admin.ModelAdmin):
    list_display = ("ticket", "author_role", "is_internal", "created_at")
    list_filter = ("author_role", "is_internal")
    search_fields = ("ticket__reference", "body")
    readonly_fields = ("created_at",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "title", "read_at", "created_at")
    list_filter = ("kind",)
    search_fields = ("title", "user__email")
