"""What each plan includes, enforced on the server.

- Service categories: a plan includes every category whose ``minimum_plan_rank``
  is at or below the plan's ``rank`` (Starter 1, Growth 2, Dedicated 3). A
  category with rank 0 is open to everyone, including clients without a plan,
  whose requests are quoted one by one.
- Legal & IP reviews come from their own allowance, counted per window: every
  month, or every quarter on Starter. Beyond it, legal work is quoted first.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Sum
from django.utils import timezone

from apps.core.exceptions import ValidationFailure

from .models import Plan, Subscription, SubscriptionStatus, UsageKind, UsageLedger


def active_subscription(organization) -> Subscription | None:
    from .services import active_subscription as _active

    return _active(organization)


def plan_for_rank(rank: int) -> Plan | None:
    """The smallest active plan that reaches ``rank``, to name in messages."""
    return Plan.objects.filter(is_active=True, rank__gte=rank).order_by("rank", "price_minor").first()


def category_allowed(organization, category) -> bool:
    minimum = getattr(category, "minimum_plan_rank", 0) or 0
    if minimum == 0:
        return True
    subscription = active_subscription(organization)
    return subscription is not None and subscription.plan.rank >= minimum


@dataclass(frozen=True)
class CategoryAccess:
    allowed: bool
    required_plan: Plan | None = None


def category_access(organization, category) -> CategoryAccess:
    if category_allowed(organization, category):
        return CategoryAccess(True)
    return CategoryAccess(False, plan_for_rank(category.minimum_plan_rank))


def require_category(organization, category) -> None:
    access = category_access(organization, category)
    if access.allowed:
        return
    where = f"from the {access.required_plan.name} plan" if access.required_plan else "on a bigger plan"
    raise ValidationFailure(f"{category.name} is included {where}. Upgrade your plan to request it.")


def is_paused(organization) -> bool:
    """Service is paused after an unpaid renewal (Terms 5.6.4)."""
    return Subscription.objects.filter(
        organization=organization, status=SubscriptionStatus.PAST_DUE
    ).exists()


def require_not_paused(organization) -> None:
    if is_paused(organization):
        raise ValidationFailure(
            "Your service is paused because the renewal is unpaid. Pay it on the Plan page to send new requests."
        )


# ---------- legal review allowance ----------

def legal_window_start(subscription: Subscription, now=None):
    """Start of the current legal-review window, counted from when the plan began."""
    from .services import add_months

    now = now or timezone.now()
    months = max(1, subscription.plan.legal_review_period_months or 1)
    anchor = subscription.created_at
    start, k = anchor, 0
    while add_months(anchor, (k + 1) * months) <= now:
        k += 1
        start = add_months(anchor, k * months)
    return start


def legal_reviews_used(organization_id, subscription: Subscription, now=None) -> int:
    """Reviews reserved since the current window began.

    Counted by when each was reserved, not by its stored window, so moving to a
    plan with a different window (say Starter's quarter to Growth's month) never
    hands out an extra review in the same period.
    """
    return (
        UsageLedger.objects.filter(
            organization_id=organization_id,
            kind=UsageKind.LEGAL,
            created_at__gte=legal_window_start(subscription, now),
        ).aggregate(total=Sum("units"))["total"]
        or 0
    )


def legal_reviews_remaining(organization) -> int:
    subscription = active_subscription(organization)
    if subscription is None:
        return 0
    return max(0, subscription.plan.legal_review_allowance - legal_reviews_used(organization.pk, subscription))


def uses_legal_allowance(task) -> bool:
    return lane_for(task.service_version) == LANE_LEGAL


# ---------- how a request counts against the plan (Service Catalogue v2.1, Points rules) ----------

LANE_GENERAL, LANE_LEGAL, LANE_QUOTED, LANE_WORKSPACE, LANE_SCOPING = (
    "general", "legal", "quoted", "workspace", "scoping")


def lane_for(version) -> str:
    """Which allowance a service uses. Legal & IP categories keep using the legal
    allowance even for versions published before lanes. "Scoping" (Other / not sure)
    is worked out by the team first, then uses the task allowance like general work."""
    lane = getattr(version, "allowance_lane", "") or LANE_GENERAL
    # Weight 0 on a task or legal service means "Quoted" in the task bank: priced on its own.
    if lane == LANE_QUOTED or (lane in (LANE_GENERAL, LANE_LEGAL) and getattr(version, "weight", 1) == 0):
        return LANE_QUOTED
    if lane == LANE_SCOPING:
        return LANE_SCOPING
    if lane == LANE_LEGAL or version.definition.category.uses_legal_allowance:
        return LANE_LEGAL
    if lane == LANE_WORKSPACE:
        return LANE_WORKSPACE
    return LANE_GENERAL


def units_for(version, quote_version=None) -> int:
    """Tasks or legal reviews a request uses: what staff set on the quote for a bigger
    request, otherwise the catalogue weight. A workspace uses none; anything else at least one."""
    lane = lane_for(version)
    if lane == LANE_WORKSPACE:
        return 0
    if lane == LANE_QUOTED:
        return 0
    staff_units = getattr(quote_version, "plan_units", None) if quote_version is not None else None
    units = staff_units if staff_units else getattr(version, "weight", 1)
    return max(1, int(units or 1))


def balances(organization) -> dict | None:
    """What's left in both allowances, and when each window ends. None without a plan."""
    from .services import add_months, allowance_remaining, task_window_start

    subscription = active_subscription(organization)
    if subscription is None:
        return None
    months = max(1, subscription.plan.legal_review_period_months or 1)
    return {
        "general": {"total": subscription.plan.monthly_task_allowance, "left": allowance_remaining(organization),
                    "until": add_months(task_window_start(subscription), 1), "noun": "task", "period": "month"},
        "legal": {"total": subscription.plan.legal_review_allowance, "left": legal_reviews_remaining(organization),
                  "until": add_months(legal_window_start(subscription), months), "noun": "legal review",
                  "period": "quarter" if months == 3 else "month"},
    }


def preview(organization, version, units: int | None = None, *, plan_balances=False) -> dict:
    """What a request for this service will use, shown before the client sends it.

    Only a preview: the team confirms the size when they scope it, and the server
    checks the allowance again when the quote is accepted. Pass plan_balances (from
    balances()) to preview many services without recounting.
    """
    lane = lane_for(version)
    units = units if units else units_for(version)
    plan = balances(organization) if plan_balances is False else plan_balances
    out = {"lane": lane, "units": units, "plan": plan is not None}
    if lane == LANE_QUOTED:
        out["text"] = "This is quoted first. It uses none of your plan; you'll see the price before any work starts."
        return out
    if plan is None:
        out["text"] = "You don't have a plan, so this is quoted first. You'll see the price before any work starts."
        return out
    if lane == LANE_SCOPING:
        out["text"] = ("The team works out what this is first. If it fits your plan it uses your tasks; "
                       "if not, you'll see a quote before any work starts.")
        return out
    if lane == LANE_WORKSPACE:
        out["text"] = "Included in your plan. It doesn't use any of your tasks."
        return out
    pool = plan[LANE_LEGAL if lane == LANE_LEGAL else LANE_GENERAL]
    total, left, until, noun, period = pool["total"], pool["left"], pool["until"], pool["noun"], pool["period"]
    after = left - units
    out.update(total=total, left=left, after=after, until=until, fits=after >= 0, noun=noun)
    nouns = noun + ("s" if units != 1 else "")
    if after >= 0:
        out["text"] = (f"This will use {units} {nouns} of your {total} ({after} left this {period}, "
                       f"until {until:%d %b}).")
    else:
        out["text"] = (f"This uses {units} {nouns}, and you have {left} left this {period}. "
                       "Send it anyway: we'll quote the extra before any work starts.")
    return out


def lock_active_subscription(organization_id):
    now = timezone.now()
    subscription = (
        Subscription.objects.select_for_update()
        .filter(organization_id=organization_id, status=SubscriptionStatus.ACTIVE)
        .select_related("plan")
        .first()
    )
    from .services import in_paid_time

    if subscription is None or not in_paid_time(subscription, now):
        return None
    return subscription


# ---------- where the work is for ----------

def outside_plan_region(task) -> bool:
    """True when a naira or Africa plan is asked for work for a country outside Africa.

    Such work isn't covered by the plan: it's quoted as a one-off fee, or the business
    moves to the international plan. The price follows where the work is for, not
    where the client says they are, and staff confirm the country before quoting.
    """
    from apps.core.countries import BY_CODE, REGION_INTERNATIONAL

    code = (task.work_country or "").upper()
    if not code or code not in BY_CODE or BY_CODE[code][2]:
        return False
    return task.organization.region != REGION_INTERNATIONAL
