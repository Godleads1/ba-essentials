"""Closing a business account (Terms 5.5.3, Privacy Policy section 5).

The owner asks to close. The plan stops renewing and nothing more is charged;
full access runs to the end of the paid period, then the account closes and is
read-only (sign in, view and download). The retention job deletes the account
and business profile 90 days after it closes.
"""
from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit_service
from apps.core.exceptions import PermissionFailure, ValidationFailure

from .models import Membership, MembershipRole, Organization


def _owners(organization):
    return [m.user for m in Membership.objects.filter(organization=organization, role=MembershipRole.OWNER,
                                                       user__is_active=True).select_related("user")]


def _tell_owners(organization, subject: str, body: str) -> None:
    from apps.communications.models import NotificationKind
    from apps.communications.services import notify
    from apps.core.models import OutboxEvent

    owners = _owners(organization)
    notify(owners, organization_id=organization.pk, kind=NotificationKind.BILLING, title=subject)
    emails = [u.email for u in owners if u.email]
    if emails:
        OutboxEvent.objects.create(topic=OutboxEvent.Topic.EMAIL, payload={
            "to": emails, "subject": f"B&A Essentials: {subject}",
            "body": body + f"\n\nQuestions: {settings.SUPPORT_EMAIL}\n",
        })


def _require_owner(membership) -> None:
    if membership is None or membership.role != MembershipRole.OWNER:
        raise PermissionFailure("Only the business owner can close the account.")


def open_requests(organization) -> int:
    from apps.tasks.models import OPEN_STATUSES, Task

    return Task.objects.filter(organization=organization, status__in=OPEN_STATUSES).count()


def paid_until(organization):
    """When a closure asked for now would take effect: the end of the paid period."""
    from apps.billing.models import Subscription, SubscriptionStatus

    sub = Subscription.objects.filter(organization=organization, status=SubscriptionStatus.ACTIVE).first()
    now = timezone.now()
    return sub.period_end if sub is not None and sub.period_end > now else now


def request_closure(organization, membership) -> Organization:
    from apps.billing import renewals
    from apps.billing.models import Subscription, SubscriptionStatus

    _require_owner(membership)
    with transaction.atomic():
        org = Organization.objects.select_for_update().get(pk=organization.pk)
        if org.closed_at is not None or org.closure_requested_at is not None:
            return org
        now = timezone.now()
        sub = Subscription.objects.select_for_update().filter(
            organization=org, status__in=(SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE)
        ).first()
        stopped = False
        if sub is not None and (sub.status == SubscriptionStatus.PAST_DUE or sub.period_end <= now):
            # The paid period is over and the renewal unpaid (grace or paused). Plans don't renew
            # by themselves, so nothing is owed: the plan ends and the account closes today.
            sub.status = SubscriptionStatus.CANCELLED
            sub.save(update_fields=["status", "updated_at"])
            renewals.end_unrenewed(sub)
            closes_at = now
        elif sub is not None:
            if not sub.cancel_at_period_end:
                renewals.cancel_renewal(org, membership)
                stopped = True
            closes_at = sub.period_end
        else:
            closes_at = now
        org.closure_requested_at, org.closure_requested_by = now, membership.user
        org.closes_at, org.closure_stopped_renewal = closes_at, stopped
        org.save(update_fields=["closure_requested_at", "closure_requested_by", "closes_at",
                                "closure_stopped_renewal", "updated_at"])
        audit_service.record(action="account.closure_requested", actor=membership.user, organization=org,
                             resource_type="organization", resource_id=str(org.pk),
                             metadata={"closes_at": closes_at.isoformat(), "renewal_stopped": "yes" if stopped else "no"})
        if closes_at <= now:
            _close(org, now)
        else:
            _tell_owners(org, "Your account will close",
                         f"You asked to close the {org.name} account. Your plan won't renew and nothing more "
                         f"will be charged. You have full access until {closes_at:%d %b %Y}, then the account "
                         "closes. You can change your mind until then in Profile & security.")
    return org


def cancel_closure(organization, membership) -> Organization:
    from apps.billing import renewals

    _require_owner(membership)
    with transaction.atomic():
        org = Organization.objects.select_for_update().get(pk=organization.pk)
        if org.closure_requested_at is None or org.closed_at is not None:
            raise ValidationFailure("This account isn't waiting to close.")
        restart = org.closure_stopped_renewal
        org.closure_requested_at = org.closure_requested_by = org.closes_at = None
        org.closure_stopped_renewal = False
        org.save(update_fields=["closure_requested_at", "closure_requested_by", "closes_at",
                                "closure_stopped_renewal", "updated_at"])
        if restart:
            renewals.undo_cancel(org, membership)
        audit_service.record(action="account.closure_cancelled", actor=membership.user, organization=org,
                             resource_type="organization", resource_id=str(org.pk),
                             metadata={"renewal_restarted": "yes" if restart else "no"})
    return org


def _close(org: Organization, now) -> None:
    org.closed_at = now
    org.save(update_fields=["closed_at", "updated_at"])
    audit_service.record(action="account.closed", organization=org, resource_type="organization",
                         resource_id=str(org.pk))
    _tell_owners(org, "Your account is closed",
                 f"The {org.name} account is now closed. You can still sign in to view and download your "
                 "files and invoices. Your account and business profile will be deleted 90 days from today. "
                 "Invoices and tax records are kept for 7 years, and task documents for 6 years, as the law "
                 "and our Privacy Policy require.")


def close_due(now=None) -> int:
    """Close accounts whose paid period has ended. Safe to run repeatedly."""
    now = now or timezone.now()
    closed = 0
    for pk in Organization.objects.filter(closure_requested_at__isnull=False, closed_at__isnull=True,
                                          closes_at__lte=now).values_list("pk", flat=True):
        with transaction.atomic():
            org = Organization.objects.select_for_update().get(pk=pk)
            if org.closed_at is None:
                _close(org, now)
                closed += 1
    return closed
