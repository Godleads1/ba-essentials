from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect

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
        if membership.organization.closed_at is not None and request.method not in ("GET", "HEAD", "OPTIONS"):
            # A closed account is read-only: sign in, view and download (Privacy Policy 5).
            messages.error(request, "This account is closed, so it's read-only. Contact support to reopen it.")
            return redirect("client-overview")
        if request.method not in ("GET", "HEAD", "OPTIONS") and _paused_and_blocked(request):
            messages.error(request, "Your account is paused because the renewal is unpaid. "
                                    "Pay the renewal to continue.")
            return redirect("plan-home")
        return view(request, *args, **kwargs)

    return wrapper


# While an account is paused (renewal unpaid after the 5-business-day grace), clients can still sign in,
# view and download, and these actions stay open: paying, account security and data rights,
# closing the account, switching business, and asking support for help.
PAUSED_ALLOWED = frozenset({
    "invoice-checkout", "plan-checkout", "switch-organization", "notifications-read",
    "support-new", "support-detail", "support-close",
    "security-password", "security-mfa", "security-mfa-off", "security-details", "security-preferences",
    "security-data-download", "security-data-request", "security-close", "security-close-cancel",
    "security-channel", "security-email", "security-phone", "security-phone-remove",
    "security-sessions-others", "security-session-revoke",
})


def _paused_and_blocked(request) -> bool:
    from apps.billing.plan_rules import is_paused

    match = getattr(request, "resolver_match", None)
    if match is not None and match.url_name in PAUSED_ALLOWED:
        return False
    return is_paused(request.organization)


def organization_scope(request) -> Organization:
    membership = require_membership(request)
    return membership.organization


def role_label(membership: Membership) -> str:
    return MembershipRole(membership.role).label
