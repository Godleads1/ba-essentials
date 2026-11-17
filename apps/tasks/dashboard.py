"""What the client dashboard shows, worked out on the server from the business's own requests."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.core.client_status import ACTION

from .models import OPEN_STATUSES, TaskStatus

IN_PROGRESS = (TaskStatus.READY_FOR_ASSIGNMENT, TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS,
               TaskStatus.REWORK_REQUIRED, TaskStatus.REVISION_REQUESTED)
BEING_CHECKED = (TaskStatus.INTERNAL_REVIEW, TaskStatus.APPROVED_FOR_RELEASE)
DELIVERED = (TaskStatus.DELIVERED, TaskStatus.COMPLETED)
WAITING_ON_CLIENT = (TaskStatus.NEEDS_INFORMATION, TaskStatus.AWAITING_CLIENT_APPROVAL,
                     TaskStatus.AWAITING_PAYMENT, TaskStatus.DELIVERED)
DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"]


def greeting(now) -> str:
    hour = timezone.localtime(now).hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


def stats(tasks, now) -> dict:
    local = timezone.localtime(now)
    month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    counts = tasks.aggregate(
        total=Count("id"),
        in_progress=Count("id", filter=Q(status__in=IN_PROGRESS)),
        checking=Count("id", filter=Q(status__in=BEING_CHECKED)),
        delivered=Count("id", filter=Q(status__in=DELIVERED)),
        this_month=Count("id", filter=Q(created_at__gte=month_start)),
    )
    return counts


def week(tasks, now) -> dict:
    """Requests sent each day this week, Monday first. Days still to come are empty."""
    local = timezone.localtime(now)
    monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    per_day = [0] * 7
    for created in tasks.filter(created_at__gte=monday).values_list("created_at", flat=True):
        per_day[timezone.localtime(created).weekday()] += 1
    today = local.weekday()
    top = max(per_day + [4])
    days = []
    for i, letter in enumerate(DAY_LETTERS):
        future = i > today
        n = 0 if future else per_day[i]
        height = 62 if future else max(18, round(n / top * 100))
        tone = "bg-brand-orange" if i == today else ("stripes" if future or n == 0 else "bg-brand-navy")
        days.append({"letter": letter, "count": n, "height": height, "tone": tone, "today": i == today,
                     "future": future})
    return {"days": days, "total": sum(d["count"] for d in days)}


def waiting_on_you(tasks):
    task = (tasks.filter(status__in=WAITING_ON_CLIENT)
            .select_related("service_version__definition__category").order_by("due_date", "updated_at").first())
    if task is None:
        return None
    return {"task": task, "action": ACTION.get(task.status, "Take a look")}


def deadlines(tasks, limit: int = 4):
    return list(tasks.filter(status__in=OPEN_STATUSES, due_date__isnull=False)
                .select_related("service_version__definition").order_by("due_date")[:limit])


def plan_usage(organization, subscription) -> dict | None:
    """The allowance gauge: delivered and in-progress requests this period, out of the plan's allowance."""
    if subscription is None:
        return None
    from apps.billing.models import UsageKind, UsageLedger
    from apps.billing.plan_rules import legal_reviews_remaining
    from apps.billing.services import task_window_start

    total = subscription.plan.monthly_task_allowance
    rows = (UsageLedger.objects.filter(organization=organization, kind=UsageKind.ALLOWANCE,
                                       period_start=task_window_start(subscription))
            .select_related("task"))
    delivered = sum(r.units for r in rows if r.task_id and r.task.status in DELIVERED)
    used = sum(r.units for r in rows)
    in_progress = max(0, used - delivered)
    def pct(n):
        return round(n / total * 100, 2) if total else 0

    legal_total = subscription.plan.legal_review_allowance
    return {
        "total": total, "used": used, "percent": round(used / total * 100) if total else 0,
        "done_dash": pct(min(delivered, total)), "progress_dash": pct(min(in_progress, max(0, total - delivered))),
        "legal_total": legal_total,
        "legal_used": max(0, legal_total - legal_reviews_remaining(organization)) if legal_total else 0,
        "legal_every": subscription.plan.legal_review_period_months,
    }


# ---------- one request: the client's progress timeline ----------

FLOW = [("submitted", "Request submitted"), ("scoping", "Reviewed and scoped"), ("progress", "Work in progress"),
        ("checking", "Quality check"), ("delivered", "Delivered")]
STEP_OF = {"submitted": 0, "scoping": 1, "input": 2, "progress": 2, "hold": 2, "checking": 3, "delivered": 4}


def timeline(task, events) -> list[dict]:
    """Steps with real dates from the status history. Waiting on the client or on
    hold shows as an extra orange step; a cancelled request stops."""
    from apps.core import client_status as cs

    group = cs.group(task.status)
    first_in: dict[str, object] = {"submitted": task.created_at}
    last_wait = None
    for e in sorted(events, key=lambda e: e.created_at):
        g = cs.group(e.to_status)
        g = "scoping" if e.to_status in ("awaiting_client_approval", "awaiting_payment") else g
        first_in.setdefault(g, e.created_at)
        if g in ("input", "hold"):
            last_wait = e.created_at
    if group == "cancelled":
        return [{"label": "Request submitted", "state": "done", "date": task.created_at},
                {"label": "Cancelled", "state": "stop", "date": task.updated_at}]
    at = STEP_OF.get(group, 0)
    if task.status in ("awaiting_client_approval", "awaiting_payment"):
        at = 1
    steps = []
    for i, (g, label) in enumerate(FLOW):
        state = "done" if i < at else "now" if i == at else "next"
        if i == 4 and group == "delivered":
            state = "done"
        steps.append({"label": label, "state": state, "date": first_in.get(g) if i <= at else None})
    if group in ("input", "hold") or task.status in ("awaiting_client_approval", "awaiting_payment"):
        steps.insert(min(at + 1, len(steps)), {"label": cs.label(task.status), "state": "wait",
                                               "date": last_wait or task.updated_at})
    return steps
