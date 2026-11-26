"""The monthly summary for Growth and Dedicated plans (pricing page: "Monthly performance summary").

Figures for one calendar month, Lagos time:
- sent: requests the business sent (drafts don't count);
- delivered: requests first delivered that month;
- turnaround: average business days (9am-5pm, Mon-Fri, minus SUPPORT_HOLIDAYS) from
  sending to first delivery, for the requests delivered that month;
- plan used: plan tasks and legal reviews taken from the allowance that month;
- support: first replies on or before their target, among tickets opened that month
  that have been answered or are already past their target.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time

from django.db.models import Min, Sum
from django.utils import timezone

from apps.core.business_time import HOURS_PER_DAY, business_hours_between

# Plans from this rank get the summary (Growth 2, Dedicated 3).
SUMMARY_RANK = 2


@dataclass
class MonthSummary:
    month: date
    start: datetime
    end: datetime
    complete: bool
    sent: int = 0
    delivered: int = 0
    turnaround_days: float | None = None
    plan_used: int = 0
    legal_used: int = 0
    allowance: int | None = None
    legal_allowance: int | None = None
    support_on_time: int = 0
    support_counted: int = 0
    reply_target: str = ""
    delivered_tasks: list = field(default_factory=list)
    by_service: list = field(default_factory=list)
    period_end: datetime | None = None

    @property
    def empty(self) -> bool:
        return not (self.sent or self.delivered)

    @property
    def support_percent(self) -> int | None:
        return round(self.support_on_time / self.support_counted * 100) if self.support_counted else None

    @property
    def unused(self) -> int | None:
        if self.allowance is None or not self.complete:
            return None
        return max(0, self.allowance - self.plan_used)


def month_bounds(month: date) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    start = datetime.combine(month.replace(day=1), time(0, 0), tzinfo=tz)
    nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return start, datetime.combine(nxt, time(0, 0), tzinfo=tz)


def this_month(now=None) -> date:
    return timezone.localtime(now or timezone.now()).date().replace(day=1)


def previous_month(month: date) -> date:
    return date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1)


def next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def available_months(organization, now=None) -> tuple[date, date]:
    """(first, last) months that can be shown: from sign-up to the current month."""
    first = timezone.localtime(organization.created_at).date().replace(day=1)
    return first, this_month(now)


def plan_rank(organization) -> int:
    from apps.billing.services import active_subscription

    subscription = active_subscription(organization)
    return subscription.plan.rank if subscription else 0


def build(organization, month: date, now=None) -> MonthSummary:
    from apps.billing.models import UsageKind, UsageLedger
    from apps.billing.services import active_subscription
    from apps.communications.models import SupportTicket
    from apps.communications.service_levels import target_label
    from apps.tasks.models import Task, TaskStatus, TaskStatusEvent

    now = now or timezone.now()
    start, end = month_bounds(month)
    s = MonthSummary(month=month, start=start, end=end, complete=end <= now)

    s.sent = (Task.objects.filter(organization=organization, created_at__gte=start, created_at__lt=end)
              .exclude(status=TaskStatus.DRAFT).count())

    firsts = (TaskStatusEvent.objects.filter(task__organization=organization, to_status=TaskStatus.DELIVERED)
              .values("task").annotate(first=Min("created_at")).filter(first__gte=start, first__lt=end))
    delivered_at = {row["task"]: row["first"] for row in firsts}
    tasks = list(Task.objects.filter(pk__in=delivered_at)
                 .select_related("service_version__definition__category"))
    s.delivered = len(tasks)
    if tasks:
        hours = [business_hours_between(t.created_at, delivered_at[t.pk]) for t in tasks]
        s.turnaround_days = round(sum(hours) / len(hours) / HOURS_PER_DAY, 1)
    tasks.sort(key=lambda t: delivered_at[t.pk])
    s.delivered_tasks = [{"task": t, "delivered_at": delivered_at[t.pk],
                          "category": t.service_version.definition.category.name} for t in tasks]
    s.by_service = Counter(t.service_version.definition.category.name for t in tasks).most_common()

    usage = UsageLedger.objects.filter(organization=organization, created_at__gte=start, created_at__lt=end)
    s.plan_used = usage.filter(kind=UsageKind.ALLOWANCE).aggregate(n=Sum("units"))["n"] or 0
    s.legal_used = usage.filter(kind=UsageKind.LEGAL).aggregate(n=Sum("units"))["n"] or 0

    subscription = active_subscription(organization)
    if subscription:
        s.allowance = subscription.plan.monthly_task_allowance
        s.legal_allowance = subscription.plan.legal_review_allowance or None
        s.period_end = subscription.period_end
        s.reply_target = target_label(subscription.plan.rank)

    tickets = SupportTicket.objects.filter(organization=organization, created_at__gte=start, created_at__lt=end,
                                           reply_due_at__isnull=False)
    for due, replied in tickets.values_list("reply_due_at", "first_reply_at"):
        if replied is not None:
            s.support_counted += 1
            s.support_on_time += replied <= due
        elif due < now:
            s.support_counted += 1
    return s
