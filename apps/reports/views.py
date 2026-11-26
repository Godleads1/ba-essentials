from __future__ import annotations

from datetime import date

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.organizations.access import client_required

from . import summary


def _parse_month(raw: str) -> date | None:
    try:
        year, month = (int(x) for x in (raw or "").split("-"))
        return date(year, month, 1)
    except (TypeError, ValueError):
        return None


@client_required
@require_GET
def monthly_summary(request):
    """One month's summary for Growth and Dedicated; others see what it offers."""
    organization = request.organization
    rank = summary.plan_rank(organization)
    if rank < summary.SUMMARY_RANK:
        return render(request, "banda/app/summary.html", {"locked": True})
    first, last = summary.available_months(organization)
    month = _parse_month(request.GET.get("month", ""))
    if month is None or not first <= month <= last:
        # Default: last month once there is one, else this month so far.
        month = summary.previous_month(last) if first < last else last
    data = summary.build(organization, month)
    return render(request, "banda/app/summary.html", {
        "s": data,
        "plan_name": "Dedicated" if rank >= 3 else "Growth",
        "prev": summary.previous_month(month) if month > first else None,
        "next": summary.next_month(month) if month < last else None,
        "max_service": data.by_service[0][1] if data.by_service else 1,
    })
