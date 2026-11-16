from __future__ import annotations

from django.contrib import admin

from .models import AccountFlag, Invitation, Membership, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "country", "international_plan", "status", "created_at")
    list_filter = ("status", "country", "international_plan")
    search_fields = ("name", "slug")
    # Closure dates are set by the client's request and the retention job, not by hand.
    readonly_fields = ("closure_requested_at", "closure_requested_by", "closes_at", "closed_at",
                       "closure_stopped_renewal", "profile_deleted_at")

    def save_model(self, request, obj, form, change):
        # The country sets the pricing currency, so a change is always on record.
        old = Organization.objects.get(pk=obj.pk) if change else None
        old_country = old.country if old else ""
        super().save_model(request, obj, form, change)
        if change and "international_plan" in form.changed_data:
            from apps.audit import services as audit_service

            audit_service.record(
                action="organization.international_plan_changed",
                actor=request.user,
                organization=obj,
                resource_type="organization",
                resource_id=str(obj.pk),
                metadata={"from": old.international_plan, "to": obj.international_plan},
            )
        if change and "legal_hold" in form.changed_data:
            from apps.audit import services as audit_service

            audit_service.record(
                action="organization.legal_hold_changed",
                actor=request.user,
                organization=obj,
                resource_type="organization",
                resource_id=str(obj.pk),
                metadata={"to": "on" if obj.legal_hold else "off"},
            )
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


@admin.register(AccountFlag)
class AccountFlagAdmin(admin.ModelAdmin):
    list_display = ("organization", "kind", "detail", "created_at", "resolved_at")
    list_filter = ("kind", "resolved_at")
    readonly_fields = ("organization", "kind", "detail", "created_at", "resolved_at", "resolved_by", "resolution")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
