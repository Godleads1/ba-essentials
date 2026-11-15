from __future__ import annotations

from django.contrib import admin

from .models import Invitation, Membership, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "country", "status", "created_at")
    list_filter = ("status", "country")
    search_fields = ("name", "slug")

    def save_model(self, request, obj, form, change):
        # The country sets the pricing currency, so a change is always on record.
        old_country = Organization.objects.get(pk=obj.pk).country if change else ""
        super().save_model(request, obj, form, change)
        if change and "country" in form.changed_data:
            from apps.audit import services as audit_service

            audit_service.record(
                action="organization.country_changed",
                actor=request.user,
                organization=obj,
                resource_type="organization",
                resource_id=str(obj.pk),
                metadata={"from": old_country, "to": obj.country},
            )


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("organization", "user", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("organization__name", "user__email")


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "organization", "role", "created_at", "expires_at", "accepted_at")
    list_filter = ("role",)
    search_fields = ("email", "organization__name")
    exclude = ("token_hash",)
    readonly_fields = ("created_at", "expires_at", "accepted_at", "revoked_at")
