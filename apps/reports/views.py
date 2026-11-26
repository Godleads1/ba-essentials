from __future__ import annotations

import csv
from datetime import date

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.audit import services as audit_service
from apps.organizations.access import client_required

from . import reports, summary


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
        "has_reports": rank >= reports.REPORTS_RANK,
    })


def _period(request) -> int:
    try:
        months = int(request.GET.get("months", reports.DEFAULT_PERIOD))
    except (TypeError, ValueError):
        return reports.DEFAULT_PERIOD
    return months if months in reports.PERIODS else reports.DEFAULT_PERIOD


@client_required
@require_GET
def reports_home(request):
    """Dedicated: trends over the last 3, 6 or 12 finished months. Others see what it offers."""
    organization = request.organization
    if summary.plan_rank(organization) < reports.REPORTS_RANK:
        return render(request, "banda/app/reports.html", {"locked": True})
    months = _period(request)
    report = reports.build(organization, months)
    return render(request, "banda/app/reports.html", {
        "r": report, "months": months, "periods": reports.PERIODS, "period_label": reports.period_label(report),
    })


@client_required
@require_GET
def reports_csv(request):
    """Every number on the Reports page, one row per month, as a spreadsheet."""
    organization = request.organization
    if summary.plan_rank(organization) < reports.REPORTS_RANK:
        raise Http404("Reports come with the Dedicated plan.")
    months = _period(request)
    report = reports.build(organization, months)
    audit_service.record(action="reports.exported", actor=request.user, organization_id=organization.pk,
                         resource_type="organization", resource_id=str(organization.pk),
                         metadata={"months": months})
    name = "reports.csv"
    if report.months:
        name = f"reports-{report.months[0]:%Y-%m}_{report.months[-1]:%Y-%m}.csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response.write("\ufeff")  # so Excel reads the naira sign and accents correctly
    writer = csv.writer(response)
    for row in reports.csv_rows(report):
        writer.writerow(row)
    return response
