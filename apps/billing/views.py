from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.billing import services as billing
from apps.billing.models import Invoice, UsageLedger
from apps.organizations.access import client_required


@client_required
@require_GET
def plan_home(request):
    subscription = billing.active_subscription(request.organization)
    usage = UsageLedger.objects.filter(
        organization=request.organization
    ).select_related("task")
    if subscription is not None:
        usage = usage.filter(period_start=subscription.period_start)
    return render(
        request,
        "app/plan.html",
        {
            "subscription": subscription,
            "allowance_remaining": billing.allowance_remaining(request.organization),
            "usage": usage.order_by("-created_at")[:100],
        },
    )


@client_required
@require_GET
def billing_home(request):
    invoices = (
        Invoice.objects.filter(organization=request.organization)
        .select_related("task", "quote_version")
        .order_by("-issued_at")[:100]
    )
    return render(
        request,
        "app/billing.html",
        {
            "invoices": invoices,
            "subscription": billing.active_subscription(request.organization),
            "allowance_remaining": billing.allowance_remaining(request.organization),
        },
    )
