"""The monthly summary email: queued on the 1st for the month before (cron, deploy/banda.cron).

Sent to the business owners on Growth and Dedicated who keep "Monthly summary email" on.
It carries the four headline numbers only, never request names or files, and links to the
summary page. A month with no requests sends nothing. Each business gets it once a month.
"""
from __future__ import annotations

import logging
from datetime import date

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import summary
from .models import MonthlySummarySent

logger = logging.getLogger("ba.reports")


def _recipients(organization):
    from apps.organizations.models import Membership, MembershipRole

    owners = (Membership.objects.filter(organization=organization, role=MembershipRole.OWNER)
              .select_related("user"))
    return [m.user for m in owners if m.user.is_active and m.user.is_client_account and m.user.summary_by_email
            and m.user.email_verified and m.user.email]


def _body(user, organization, s) -> str:
    link = f"{settings.APP_BASE_URL.rstrip('/')}/app/summary/?month={s.month:%Y-%m}"
    turnaround = f"{s.turnaround_days} business days" if s.turnaround_days is not None else "none delivered"
    support = f"{s.support_percent}%" if s.support_percent is not None else "no support requests"
    used = f"{s.plan_used} of {s.allowance}" if s.allowance else str(s.plan_used)
    return (
        f"Hello {user.full_name or ''},\n\n"
        f"Here's what we did for {organization.name} in {s.month:%B %Y}.\n\n"
        f"- Requests delivered: {s.delivered} (from {s.sent} sent)\n"
        f"- Plan tasks used: {used}\n"
        f"- Average turnaround: {turnaround}\n"
        f"- Support replies on time: {support}\n\n"
        f"See the full summary: {link}\n\n"
        "You get this because you own a Growth or Dedicated plan. Turn it off in Profile & security: "
        f"{settings.APP_BASE_URL.rstrip('/')}/app/security/#contact\n"
    )


def send_for_month(month: date, now=None) -> dict:
    """Queue last month's summary for every eligible business. Safe to run again."""
    from apps.billing.models import Subscription, SubscriptionStatus
    from apps.core.models import OutboxEvent

    now = now or timezone.now()
    counts = {"queued": 0, "skipped_empty": 0, "already_sent": 0, "no_recipient": 0}
    subscriptions = (Subscription.objects.filter(status=SubscriptionStatus.ACTIVE,
                                                 plan__rank__gte=summary.SUMMARY_RANK)
                     .select_related("organization", "plan"))
    for sub in subscriptions:
        organization = sub.organization
        if summary.plan_rank(organization) < summary.SUMMARY_RANK:
            continue  # lapsed or outside its paid time
        if MonthlySummarySent.objects.filter(organization=organization, month=month).exists():
            counts["already_sent"] += 1
            continue
        s = summary.build(organization, month, now=now)
        if s.empty:
            counts["skipped_empty"] += 1
            continue
        users = _recipients(organization)
        if not users:
            counts["no_recipient"] += 1
            continue
        try:
            with transaction.atomic():
                MonthlySummarySent.objects.create(organization=organization, month=month, recipients=len(users))
                OutboxEvent.objects.bulk_create([OutboxEvent(topic=OutboxEvent.Topic.EMAIL, payload={
                    "to": [u.email],
                    "subject": f"Your {month:%B} summary from B&A Essentials",
                    "body": _body(u, organization, s),
                }) for u in users])
        except IntegrityError:  # another run got there first
            counts["already_sent"] += 1
            continue
        counts["queued"] += 1
    logger.info("monthly summaries for %s: %s", f"{month:%Y-%m}", counts)
    return counts
