from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse

from apps.core.exceptions import PermissionFailure

from .models import Membership, MembershipRole, Organization


def memberships_for(user):
    return Membership.objects.filter(
        user=user, organization__status="active"
    ).select_related("organization")


def membership_for(user, organization_id) -> Membership | None:
    try:
        return memberships_for(user).filter(organization_id=organization_id).first()
    except (ValidationError, ValueError):
        return None


def current_membership(request) -> Membership | None:
    cached = getattr(request, "_membership", None)
    if cached is not None:
        return cached
    membership = None
    org_id = request.session.get("organization_id")
    if org_id:
        membership = membership_for(request.user, org_id)
    if membership is None:
        membership = memberships_for(request.user).first()
        if membership is not None:
            request.session["organization_id"] = str(membership.organization_id)
    request._membership = membership
    return membership


def require_membership(request) -> Membership:
    membership = current_membership(request)
    if membership is None:
        raise PermissionFailure("You do not have access to an organization.")
    return membership


def require_owner(membership: Membership) -> None:
    if not membership.is_owner:
        raise PermissionFailure("Only an organization owner can do that.")


def client_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if getattr(request.user, "staff_role", ""):
            return redirect("staff-dashboard")
        membership = current_membership(request)
        if membership is None:
            return redirect("organization-onboarding")
        request.membership = membership
        request.organization = membership.organization
        return view(request, *args, **kwargs)

    return wrapper


def organization_scope(request) -> Organization:
    membership = require_membership(request)
    return membership.organization


def role_label(membership: Membership) -> str:
    return MembershipRole(membership.role).label
