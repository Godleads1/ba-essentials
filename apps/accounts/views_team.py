"""The staff Team screen and the page where an invited person sets their password.

Every change is checked again in apps/accounts/team.py; these views add the "confirm it's
you" step and turn errors into messages.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core import ratelimit
from apps.core.client_ip import client_ip
from apps.core.exceptions import PermissionFailure, RateLimited, ValidationFailure

from . import services, team
from .decorators import staff_permission_required
from .models import StaffRequestStatus, StaffRole
from .permissions import STAFF_MANAGE

JOIN_POSTS_PER_IP_PER_HOUR = 20


@staff_permission_required(STAFF_MANAGE)
@require_GET
def team_home(request):
    me = request.user
    pending = list(team.open_requests())
    awaiting = [r for r in pending if r.status == StaffRequestStatus.AWAITING_APPROVAL]
    return render(request, "banda/staff/team.html", {
        "members": list(team.team_members()),
        "for_me": [r for r in awaiting if r.requested_by_id != me.pk and r.user_id != me.pk and r.email != me.email],
        "mine": [r for r in awaiting if r.requested_by_id == me.pk],
        "other_waiting": [r for r in awaiting if r.requested_by_id != me.pk and (r.user_id == me.pk or r.email == me.email)],
        "invited": [r for r in pending if r.status == StaffRequestStatus.INVITED],
        "roles": [(value, label, team.ROLE_SUMMARIES.get(value, ""), team.needs_approval(value))
                  for value, label in StaffRole.choices],
        "admin_count": team.active_admins().count(),
        "invite_days": team.INVITE_DAYS,
        "approval_days": team.APPROVAL_DAYS,
    })


def _act(request, action, done: str):
    """Run a Team change after a fresh "confirm it's you"."""
    if not services.has_fresh_step_up(request, request.user):
        messages.warning(request, "Confirm it's you, then make the change again.")
        return redirect(f"{reverse('step-up')}?{urlencode({'next': reverse('staff-team')})}")
    try:
        result = action()
    except (ValidationFailure, PermissionFailure) as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, done(result) if callable(done) else done)
    return redirect("staff-team")


def _waiting_or(done: str):
    return lambda req: ("Saved. It needs a second Admin's approval: we've emailed the other Admins."
                        if req is not None else done)


@staff_permission_required(STAFF_MANAGE)
@require_POST
def team_invite(request):
    email, role = request.POST.get("email", ""), request.POST.get("role", "")
    return _act(request, lambda: team.invite(request.user, email, role), lambda req: (
        "Saved. It needs a second Admin's approval before the invitation is sent: we've emailed the other Admins."
        if req.status == StaffRequestStatus.AWAITING_APPROVAL else f"Invitation sent to {req.email}."))


@staff_permission_required(STAFF_MANAGE)
@require_POST
def team_approve(request, request_id):
    return _act(request, lambda: team.approve(request.user, request_id), lambda req: (
        f"Approved. The invitation is on its way to {req.email}." if req.kind == "invite"
        else f"Approved. {req.email} now has the {StaffRole(req.role).label} role."))


@staff_permission_required(STAFF_MANAGE)
@require_POST
def team_reject(request, request_id):
    return _act(request, lambda: team.reject(request.user, request_id), lambda req: (
        "Cancelled." if req.status == StaffRequestStatus.CANCELLED else "Turned down."))


@staff_permission_required(STAFF_MANAGE)
@require_POST
def team_change_role(request, user_id):
    role = request.POST.get("role", "")
    return _act(request, lambda: team.change_role(request.user, user_id, role), _waiting_or("Role changed."))


@staff_permission_required(STAFF_MANAGE)
@require_POST
def team_remove(request, user_id):
    return _act(request, lambda: team.remove_access(request.user, user_id),
                lambda user: f"{user.email} no longer has access and has been signed out everywhere.")


@staff_permission_required(STAFF_MANAGE)
@require_POST
def team_restore(request, user_id):
    return _act(request, lambda: team.restore_access(request.user, user_id), _waiting_or("Access restored."))


@never_cache
@require_http_methods(["GET", "POST"])
def staff_join(request, token):
    """An invited person sets their name and password. Then they sign in and set up an authenticator."""
    if request.user.is_authenticated:
        messages.info(request, "Sign out first, then open the invitation link again.")
        return redirect("staff-dashboard" if request.user.staff_role else "client-overview")
    invitation = team.invitation_for(token)
    if invitation is None:
        return render(request, "banda/auth/staff_join.html", {"invalid": True}, status=404)
    error = ""
    if request.method == "POST":
        try:
            if not ratelimit.hit("staff-join-ip", client_ip(request) or "-", limit=JOIN_POSTS_PER_IP_PER_HOUR,
                                 window_seconds=3600):
                raise RateLimited("Too many attempts. Please wait an hour and try again.")
            password = request.POST.get("password", "")
            if password != request.POST.get("password_again", ""):
                raise ValidationFailure("The two passwords don't match.")
            team.accept_invitation(token, full_name=request.POST.get("full_name", ""), password=password)
        except (ValidationFailure, RateLimited) as exc:
            error = exc.message
        else:
            messages.success(request, "Your account is ready. Sign in, then set up your authenticator app.")
            return redirect("staff-login")
    return render(request, "banda/auth/staff_join.html", {
        "invitation": invitation, "role_label": StaffRole(invitation.role).label, "error": error,
        "full_name": request.POST.get("full_name", ""),
    })
