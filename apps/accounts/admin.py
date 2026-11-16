from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import ConsentRecord, DataRequest, DataRequestStatus, User, UserSession, VerificationChallenge


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = (
        "email",
        "full_name",
        "staff_role",
        "is_active",
        "email_verified_at",
        "mfa_enabled",
    )
    list_filter = ("staff_role", "is_active", "mfa_enabled", "is_superuser")
    search_fields = ("email", "full_name")
    readonly_fields = (
        "credential_version",
        "failed_login_count",
        "locked_until",
        "mfa_last_timestep",
        "last_login",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Profile",
            {
                "fields": (
                    "full_name",
                    "staff_role",
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "email_verified_at",
                    "mfa_enabled",
                    "mfa_confirmed_at",
                )
            },
        ),
        (
            "Security state",
            {
                "fields": (
                    "credential_version",
                    "failed_login_count",
                    "locked_until",
                    "mfa_last_timestep",
                    "last_login",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "full_name",
                    "staff_role",
                    "password1",
                    "password2",
                ),
            },
        ),
    )


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "session_tail", "created_at", "last_seen_at", "revoked_at")
    search_fields = ("user__email",)
    exclude = ("session_key",)
    readonly_fields = ("created_at", "last_seen_at")

    @admin.display(description="Session")
    def session_tail(self, obj: UserSession) -> str:
        return f"…{obj.session_key[-8:]}"


@admin.register(VerificationChallenge)
class VerificationChallengeAdmin(admin.ModelAdmin):
    list_display = ("user", "purpose", "created_at", "expires_at", "consumed_at")
    list_filter = ("purpose",)
    search_fields = ("user__email",)
    exclude = ("code_hash", "salt")
    readonly_fields = ("created_at", "expires_at", "consumed_at", "superseded_at", "attempts")


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    """Evidence of what people agreed to. Read-only: a change of mind is a new record."""

    list_display = ("user", "kind", "granted", "version", "created_at")
    list_filter = ("kind", "granted")
    search_fields = ("user__email",)
    readonly_fields = ("user", "organization", "kind", "granted", "channel", "wording", "version", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DataRequestAnswerForm(forms.ModelForm):
    class Meta:
        model = DataRequest
        fields = ("status", "outcome")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") == DataRequestStatus.DECLINED and not (cleaned.get("outcome") or "").strip():
            self.add_error("outcome", "Explain why, so the client knows and can complain to the NDPC if they wish.")
        return cleaned


@admin.register(DataRequest)
class DataRequestAdmin(admin.ModelAdmin):
    """The data protection contact answers requests here. Only the status and the
    answer can be set, once; everything is recorded in the audit log."""

    form = DataRequestAnswerForm
    list_display = ("reference", "kind", "status", "reply_by", "is_overdue", "created_at")
    list_filter = ("status", "kind")
    search_fields = ("reference", "user__email")
    readonly_fields = ("reference", "user", "organization", "kind", "details", "reply_by",
                       "created_at", "resolved_at", "resolved_by")
    fields = ("reference", "user", "organization", "kind", "details", "reply_by", "created_at",
              "status", "outcome", "resolved_at", "resolved_by")

    @admin.display(boolean=True, description="Overdue")
    def is_overdue(self, obj: DataRequest) -> bool:
        return obj.overdue

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        # Check the saved record: on a failed save the form has already changed obj.
        if obj is not None and not DataRequest.objects.filter(pk=obj.pk, status=DataRequestStatus.OPEN).exists():
            return (*self.readonly_fields, "status", "outcome")
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        from django.contrib import messages

        from apps.core.exceptions import DomainError

        from .privacy import resolve

        if not change or obj.status == DataRequestStatus.OPEN:
            return  # nothing answered yet; leave the record as it was
        try:
            resolve(DataRequest.objects.get(pk=obj.pk), request.user, status=obj.status, outcome=obj.outcome)
        except DomainError as exc:
            messages.error(request, exc.message)
