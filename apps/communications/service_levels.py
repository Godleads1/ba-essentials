"""Support reply targets by plan (Terms 3.6; the Growth and Dedicated plan promises).

The first staff reply to a new ticket is due within these business hours. A ticket
keeps the plan it was opened under, so a later upgrade or lapse doesn't move it.
"""
from __future__ import annotations

from apps.core.business_time import add_business_hours

# Plan rank (0 = no plan, 1 Starter, 2 Growth, 3 Dedicated) -> business hours to first reply.
REPLY_TARGET_HOURS = {0: 16, 1: 16, 2: 8, 3: 4}
PLAN_LABELS = {0: "No plan", 1: "Starter", 2: "Growth", 3: "Dedicated"}


def target_hours(rank: int) -> int:
    return REPLY_TARGET_HOURS.get(min(max(rank or 0, 0), 3), REPLY_TARGET_HOURS[0])


def target_label(rank: int) -> str:
    hours = target_hours(rank)
    if hours % 8 == 0:
        days = hours // 8
        return f"{days} business day{'s' if days != 1 else ''}"
    return f"{hours} business hours"


def reply_due(opened_at, rank: int):
    return add_business_hours(opened_at, target_hours(rank))


def plan_rank(organization) -> int:
    from apps.billing.services import active_subscription

    subscription = active_subscription(organization)
    return subscription.plan.rank if subscription else 0
