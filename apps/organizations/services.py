from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.audit import services as audit_service
from apps.core.exceptions import PermissionFailure, ValidationFailure

from .access import require_owner
from .models import (
    Invitation,
    Membership,
    MembershipRole,
    Organization,
    OrganizationStatus,
)

logger = logging.getLogger("ba.organizations")

INVITATION_TTL_DAYS = 7


def _unique_slug(name: str) -> str:
    base = slugify(name)[:60] or "organization"
    candidate = base
    suffix = 1
    while Organization.objects.filter(slug=candidate).exists():
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


@transaction.atomic
def create_organization(user, name: str) -> Organization:
    name = (name or "").strip()
    if len(name) < 2:
        raise ValidationFailure("Enter a business or team name.")
    organization = Organization.objects.create(
        name=name, slug=_unique_slug(name), status=OrganizationStatus.ACTIVE
    )
    Membership.objects.create(
        organization=organization, user=user, role=MembershipRole.OWNER
    )
    audit_service.record(
        action="organization.created",
        actor=user,
        organization=organization,
        resource_type="organization",
        resource_id=str(organization.pk),
    )
    return organization


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def invite_member(
    organization: Organization, inviter, email: str, role: str = MembershipRole.MEMBER
) -> tuple[Invitation, str]:
    require_owner(
        Membership.objects.get(organization=organization, user=inviter)
    )
    email = (email or "").strip().lower()
    if not email:
        raise ValidationFailure("Enter an email address to invite.")
    if role == MembershipRole.OWNER:
        raise ValidationFailure("Ownership transfer is not available from invitations.")
    if Membership.objects.filter(organization=organization, user__email=email).exists():
        raise ValidationFailure("That person is already a member of this organization.")

    token = secrets.token_urlsafe(32)
    now = timezone.now()
    with transaction.atomic():
        Invitation.objects.filter(
            organization=organization,
            email=email,
            accepted_at__isnull=True,
            revoked_at__isnull=True,
        ).update(revoked_at=now)
        invitation = Invitation.objects.create(
            organization=organization,
            email=email,
            role=role,
            token_hash=hash_token(token),
            invited_by=inviter,
            expires_at=now + timedelta(days=INVITATION_TTL_DAYS),
        )
        audit_service.record(
            action="organization.invitation_sent",
            actor=inviter,
            organization=organization,
            resource_type="invitation",
            resource_id=str(invitation.pk),
            metadata={"role": role},
        )

    link = f"{settings.APP_BASE_URL.rstrip('/')}{_accept_path(token)}"
    try:
        send_mail(
            f"Join {organization.name} on B & A Essentials",
            f"You have been invited to join {organization.name}.\n\n"
            f"Accept the invitation here: {link}\n\n"
            f"This link expires in {INVITATION_TTL_DAYS} days and works once.",
            settings.DEFAULT_FROM_EMAIL,
            [email],
        )
    except Exception:  # pragma: no cover - provider failure path
        logger.warning("invitation_email_failed invitation=%s", invitation.pk)
    return invitation, link


def _accept_path(token: str) -> str:
    from django.urls import reverse

    return reverse("invitation-accept", kwargs={"token": token})


def accept_invitation(user, token: str) -> Membership:
    if not user.email_verified_at:
        raise ValidationFailure("Verify your email address before accepting.")
    token_hash = hash_token(token or "")
    now = timezone.now()
    with transaction.atomic():
        invitation = (
            Invitation.objects.select_for_update()
            .filter(token_hash=token_hash)
            .select_related("organization")
            .first()
        )
        if (
            invitation is None
            or invitation.revoked_at is not None
            or invitation.accepted_at is not None
            or invitation.expires_at <= now
        ):
            raise ValidationFailure("This invitation is no longer valid.")
        if invitation.email.lower() != user.email.lower():
            raise ValidationFailure(
                "This invitation was sent to a different email address."
            )

        membership, created = Membership.objects.get_or_create(
            organization=invitation.organization,
            user=user,
            defaults={"role": invitation.role},
        )
        invitation.accepted_at = now
        invitation.accepted_by = user
        invitation.save(update_fields=["accepted_at", "accepted_by"])
        if created:
            audit_service.record(
                action="organization.member_joined",
                actor=user,
                organization=invitation.organization,
                resource_type="membership",
                resource_id=str(membership.pk),
            )
    return membership


def revoke_invitation(organization: Organization, invitation_id, actor) -> None:
    require_owner(Membership.objects.get(organization=organization, user=actor))
    updated = Invitation.objects.filter(
        organization=organization, pk=invitation_id, accepted_at__isnull=True
    ).update(revoked_at=timezone.now())
    if not updated:
        raise ValidationFailure("That invitation cannot be revoked.")
    audit_service.record(
        action="organization.invitation_revoked",
        actor=actor,
        organization=organization,
        resource_type="invitation",
        resource_id=str(invitation_id),
    )


def remove_member(organization: Organization, membership: Membership, actor) -> None:
    actor_membership = Membership.objects.get(organization=organization, user=actor)
    require_owner(actor_membership)
    if membership.user_id == actor.pk:
        raise ValidationFailure("You cannot remove yourself from the organization.")
    owners = Membership.objects.filter(
        organization=organization, role=MembershipRole.OWNER
    ).count()
    if membership.role == MembershipRole.OWNER and owners <= 1:
        raise ValidationFailure("An organization must keep at least one owner.")
    with transaction.atomic():
        membership.delete()
        audit_service.record(
            action="organization.member_removed",
            actor=actor,
            organization=organization,
            resource_type="membership",
            resource_id=str(membership.pk),
        )


def ensure_single_default_organization(user) -> Organization | None:
    """Convenience for tests and the demo seed only."""
    membership = Membership.objects.filter(user=user).first()
    return membership.organization if membership else None
