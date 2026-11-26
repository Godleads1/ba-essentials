"""A named account manager for a client business (the Dedicated plan's promise).

Operations or an Admin names one staff member per business on the staff Clients
page. The client sees that person on their dashboard, and new support tickets from
the business go straight to them (support staff still see every ticket).
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import StaffRole, User
from apps.accounts.permissions import CLIENT_MANAGE, has_perm
from apps.audit import services as audit_service
from apps.core.exceptions import PermissionFailure, ValidationFailure

from .models import Organization

# The same people who can answer support tickets.
MANAGER_ROLES = (StaffRole.OPERATIONS, StaffRole.SUPPORT, StaffRole.ADMIN)
# Plan rank from which the client is promised an account manager (Dedicated).
DEDICATED_RANK = 3


def eligible_managers():
    return User.objects.filter(staff_role__in=MANAGER_ROLES, is_active=True).order_by("full_name", "email")


def is_eligible(user) -> bool:
    return bool(user and user.is_active and user.staff_role in MANAGER_ROLES)


def manager_for(organization: Organization):
    """The account manager to show or route to, or None if unset or no longer eligible."""
    manager = organization.account_manager
    return manager if is_eligible(manager) else None


@transaction.atomic
def set_account_manager(*, actor, organization: Organization, manager) -> Organization:
    """Name (or, with manager=None, remove) a business's account manager."""
    if not has_perm(actor, CLIENT_MANAGE):
        raise PermissionFailure("You cannot change account managers.")
    if manager is not None and not is_eligible(manager):
        raise ValidationFailure("Choose an active Operations, Support or Admin team member.")
    caller_copy = organization
    organization = Organization.objects.select_for_update().get(pk=organization.pk)
    previous_id = organization.account_manager_id
    new_id = manager.pk if manager else None
    if previous_id == new_id:
        return organization
    organization.account_manager = manager
    organization.account_manager_since = timezone.now() if manager else None
    organization.save(update_fields=["account_manager", "account_manager_since", "updated_at"])
    caller_copy.account_manager = manager
    caller_copy.account_manager_since = organization.account_manager_since
    audit_service.record(
        action="client.account_manager_set",
        actor=actor,
        organization_id=organization.pk,
        resource_type="organization",
        resource_id=str(organization.pk),
        metadata={"previous_id": str(previous_id or ""), "manager_id": str(new_id or "")},
    )
    if manager is not None:
        from apps.communications.models import NotificationKind
        from apps.communications.services import notify

        notify([manager], organization_id=organization.pk, kind=NotificationKind.SUPPORT,
               title=f"You're now the account manager for {organization.name}", exclude=actor)
    return organization
