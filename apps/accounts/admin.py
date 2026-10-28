from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, UserSession, VerificationChallenge


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
