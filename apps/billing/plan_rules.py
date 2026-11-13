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


def legal_reviews_used(organization, subscription: Subscription) -> int:
    return (
        UsageLedger.objects.filter(
            organization=organization,
            kind=UsageKind.LEGAL,
            period_start=legal_window_start(subscription),
        ).aggregate(total=Sum("units"))["total"]
        or 0
    )


def legal_reviews_remaining(organization) -> int:
    subscription = active_subscription(organization)
    if subscription is None:
        return 0
    return max(0, subscription.plan.legal_review_allowance - legal_reviews_used(organization, subscription))


def uses_legal_allowance(task) -> bool:
    return bool(task.service_version.definition.category.uses_legal_allowance)


def lock_active_subscription(organization_id):
    now = timezone.now()
    subscription = (
        Subscription.objects.select_for_update()
        .filter(organization_id=organization_id, status=SubscriptionStatus.ACTIVE)
        .select_related("plan")
        .first()
    )
    if subscription is None or not (subscription.period_start <= now <= subscription.period_end):
        return None
    return subscription
