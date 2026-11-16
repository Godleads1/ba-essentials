"""A client's own details, contact preferences and the agreements they've given."""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit_service
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.core.legal import AGREEMENT_VERSION, PRIVACY_VERSION

from .models import ConsentKind, ConsentRecord, User

AGREEMENT_WORDING = "I agree to the Terms of Service and Privacy Policy, and I'm 18 or over."
AUTHORITY_WORDING = "I confirm I'm allowed to act for {business}."
MARKETING_WORDING = (
    "Send me tips and offers from B & A Essentials by email. "
    "I can turn this off at any time."
)


def record_consent(user, kind: str, *, granted: bool, wording: str, version: str = "",
                   organization=None, channel: str = "") -> ConsentRecord:
    consent = ConsentRecord.objects.create(
        user=user, organization=organization, kind=kind, granted=granted,
        wording=wording, version=version, channel=channel,
    )
    audit_service.record(
        action="account.consent_given" if granted else "account.consent_withdrawn",
        actor=user, organization=organization, resource_type="consent",
        resource_id=str(consent.pk), metadata={"kind": kind, "version": version, "channel": channel},
    )
    return consent


def record_agreement(user) -> ConsentRecord:
    """At sign-up, once the email code proves the address."""
    return record_consent(user, ConsentKind.AGREEMENT, granted=True,
                          wording=AGREEMENT_WORDING, version=AGREEMENT_VERSION)


def record_authority(user, organization) -> ConsentRecord:
    """When someone sets up a business workspace."""
    return record_consent(user, ConsentKind.AUTHORITY, granted=True, organization=organization,
                          wording=AUTHORITY_WORDING.format(business=organization.name),
                          version=AGREEMENT_VERSION)


@transaction.atomic
def update_details(user: User, *, full_name: str, membership=None, business_name: str | None = None) -> list[str]:
    """Change the name, and the business name for an owner. Returns what changed."""
    full_name = " ".join((full_name or "").split())
    if len(full_name) < 2:
        raise ValidationFailure("Enter your full name.")
    changed = []
    if full_name != user.full_name:
        User.objects.filter(pk=user.pk).update(full_name=full_name[:150], updated_at=timezone.now())
        user.full_name = full_name[:150]
        changed.append("full_name")
    organization = membership.organization if membership is not None else None
    if business_name is not None and organization is not None:
        business_name = " ".join(business_name.split())
        if business_name != organization.name:
            if not membership.is_owner:
                raise PermissionFailure("Only the account owner can change the business name.")
            if len(business_name) < 2:
                raise ValidationFailure("Enter your business name.")
            organization.name = business_name[:150]
            organization.save(update_fields=["name", "updated_at"])
            changed.append("business_name")
    if changed:
        # Field names only: the values are personal data and stay out of the log.
        audit_service.record(action="account.details_changed", actor=user, organization=organization,
                             resource_type="user", resource_id=str(user.pk), metadata={"fields": ",".join(changed)})
    return changed


@transaction.atomic
def set_contact_preferences(user: User, *, updates_by_email: bool, marketing_by_email: bool,
                            organization=None) -> None:
    user = User.objects.select_for_update().get(pk=user.pk)
    if updates_by_email != user.updates_by_email:
        audit_service.record(action="account.preferences_changed", actor=user, organization=organization,
                             resource_type="user", resource_id=str(user.pk),
                             metadata={"updates_by_email": "on" if updates_by_email else "off"})
    if marketing_by_email != user.marketing_by_email:
        record_consent(user, ConsentKind.MARKETING, granted=marketing_by_email, wording=MARKETING_WORDING,
                       version=PRIVACY_VERSION, channel="email", organization=organization)
    User.objects.filter(pk=user.pk).update(
        updates_by_email=updates_by_email, marketing_by_email=marketing_by_email, updated_at=timezone.now()
    )
