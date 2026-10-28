from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.billing import services as billing
from apps.billing.models import Invoice
from apps.organizations.access import client_required


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
