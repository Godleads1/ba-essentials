from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.exceptions import DomainError

from . import services
from .access import client_required, current_membership, membership_for, require_owner
from .forms import InviteMemberForm, OrganizationForm
from .models import Invitation, Membership


@login_required
@require_http_methods(["GET", "POST"])
def onboarding(request):
    if current_membership(request) is not None:
        return redirect("client-overview")
    form = OrganizationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        organization = services.create_organization(
            request.user, form.cleaned_data["name"]
        )
        request.session["organization_id"] = str(organization.pk)
        messages.success(request, f"{organization.name} is ready.")
        return redirect("client-overview")
    return render(request, "app/onboarding.html", {"form": form})


@client_required
@require_http_methods(["GET"])
def team_home(request):
    organization = request.organization
    membership = request.membership
    members = (
        Membership.objects.filter(organization=organization)
        .select_related("user")
        .order_by("role", "user__full_name")
    )
    invitations = Invitation.objects.filter(
        organization=organization,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
    )
    return render(
        request,
        "app/team.html",
        {
            "members": members,
            "invitations": invitations,
            "can_manage": membership.is_owner,
            "invite_form": InviteMemberForm(),
        },
    )


@client_required
@require_POST
def invite_member(request):
    require_owner(request.membership)
    form = InviteMemberForm(request.POST)
    if form.is_valid():
        try:
            invitation, link = services.invite_member(
                request.organization, request.user, form.cleaned_data["email"]
            )
        except DomainError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(
                request,
                f"Invitation sent to {invitation.email}. If the email does not "
                f"arrive, share this one-time link: {link}",
            )
    else:
        messages.error(request, "Enter a valid email address.")
    return redirect("team-home")


@client_required
@require_POST
def revoke_invitation(request, invitation_id):
    require_owner(request.membership)
    try:
        services.revoke_invitation(request.organization, invitation_id, request.user)
    except DomainError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "Invitation revoked.")
    return redirect("team-home")


@client_required
@require_POST
def remove_member(request, membership_id):
    require_owner(request.membership)
    membership = get_object_or_404(
        Membership, pk=membership_id, organization=request.organization
    )
    try:
        services.remove_member(request.organization, membership, request.user)
    except DomainError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "Member removed.")
    return redirect("team-home")


@login_required
@require_POST
def switch_organization(request, organization_id):
    membership = membership_for(request.user, organization_id)
    if membership is None:
        messages.error(request, "You do not have access to that organization.")
    else:
        request.session["organization_id"] = str(membership.organization_id)
    return redirect("client-overview")


@require_http_methods(["GET", "POST"])
def accept_invitation(request, token):
    if not request.user.is_authenticated:
        return redirect(f"/login/?next=/invite/{token}/")
    if not request.user.email_verified:
        request.session["pending_verify_email"] = request.user.email
        messages.info(request, "Verify your email address before accepting.")
        return redirect("verify-email")
    if request.method == "POST":
        try:
            membership = services.accept_invitation(request.user, token)
        except DomainError as exc:
            messages.error(request, exc.message)
            return redirect("client-overview")
        request.session["organization_id"] = str(membership.organization_id)
        messages.success(request, f"You joined {membership.organization.name}.")
        return redirect("client-overview")
    invitation = Invitation.objects.filter(
        token_hash=services.hash_token(token)
    ).select_related("organization").first()
    valid = (
        invitation is not None
        and invitation.accepted_at is None
        and invitation.revoked_at is None
    )
    return render(
        request,
        "app/invitation.html",
        {"invitation": invitation, "valid": valid, "token": token},
    )
