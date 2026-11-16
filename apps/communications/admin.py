from django.contrib import admin

from apps.audit import services as audit_service

from .models import CounsellingSettings, Notification, SupportTicket, TaskMessage, TicketMessage


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


@admin.register(CounsellingSettings)
class CounsellingSettingsAdmin(admin.ModelAdmin):
    """The "Need Counselling?" button: on/off, email, phone, prayer call link and time.

    Only super admins can see or change these, there is only ever one row, and
    every change is written to the audit log with the old and new values.
    """

    fields = (
        "enabled", "button_label", "counselling_email", "counselling_phone",
        "prayer_link", "prayer_when", "emergency_number", "updated_at", "updated_by",
    )
    readonly_fields = ("updated_at", "updated_by")

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "prayer_link":
            kwargs["assume_scheme"] = "https"  # a link typed without https:// is read as https
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def _super(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_module_permission(self, request):
        return self._super(request)

    def has_view_permission(self, request, obj=None):
        return self._super(request)

    def has_change_permission(self, request, obj=None):
        return self._super(request)

    def has_add_permission(self, request):
        return self._super(request) and not CounsellingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        before = {}
        if change:
            old = CounsellingSettings.objects.get(pk=obj.pk)
            before = {f: str(getattr(old, f)) for f in form.changed_data}
        obj.pk = 1
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        audit_service.record(
            action="communications.counselling_settings_changed",
            actor=request.user,
            resource_type="counselling_settings",
            resource_id="1",
            # One "old → new" entry per changed field (the audit log keeps flat values only).
            metadata={f: f"{before.get(f, '')} → {getattr(obj, f)}" for f in form.changed_data},
        )
