from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.billing import plan_rules
from apps.billing import services as billing
from apps.catalogue.models import ServiceCategory
from apps.billing.models import Invoice, PaymentAttempt, Plan, UsageLedger
from apps.core.exceptions import PermissionFailure, ValidationFailure
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
    plans = Plan.objects.filter(is_active=True, price_minor__gt=0).order_by("price_minor")
    return render(
        request,
        "app/plan.html",
        {
            "subscription": subscription,
            "allowance_remaining": billing.allowance_remaining(request.organization),
            "usage": usage.order_by("-created_at")[:100],
            "plans": plans if subscription is None else [],
            "can_buy": request.membership.role == "owner",
            "upgrades": billing.upgrade_options(request.organization),
            "legal_remaining": plan_rules.legal_reviews_remaining(request.organization),
            "legal_window_start": plan_rules.legal_window_start(subscription) if subscription else None,
            "categories": [
                (category, plan_rules.category_access(request.organization, category))
                for category in ServiceCategory.objects.filter(is_active=True)
            ],
            "payments_enabled": settings.PAYMENTS_ENABLED,
        },
    )


@client_required
@require_POST
def plan_checkout(request, plan_slug):
    """Start Paystack checkout for a plan. The price comes from the database only."""
    return_url = request.build_absolute_uri(reverse("plan-checkout-return"))
    try:
        checkout_url = billing.start_plan_checkout(
            organization=request.organization,
            membership=request.membership,
            plan_slug=plan_slug,
            return_url=return_url,
        )
    except (ValidationFailure, PermissionFailure) as exc:
        messages.error(request, exc.message)
        return redirect("plan-home")
    # paystack.initialize only returns an https address on Paystack's checkout host.
    return redirect(checkout_url)


@client_required
@require_POST
def plan_upgrade(request, plan_slug):
    """Move up to a bigger plan. Today's price comes from the server; the plan changes only once Paystack confirms."""
    return_url = request.build_absolute_uri(reverse("plan-checkout-return"))
    try:
        checkout_url = billing.start_upgrade_checkout(
            organization=request.organization,
            membership=request.membership,
            plan_slug=plan_slug,
            return_url=return_url,
        )
    except (ValidationFailure, PermissionFailure) as exc:
        messages.error(request, exc.message)
        return redirect("plan-home")
    return redirect(checkout_url)


@client_required
@require_POST
def invoice_checkout(request, invoice_id):
    """Pay a request invoice (quote or third-party fees) with Paystack.

    The browser says which invoice; the amount comes from the stored invoice only.
    """
    invoice = (
        Invoice.objects.filter(pk=invoice_id, organization=request.organization)
        .select_related("task")
        .first()
    )
    if invoice is None:
        raise Http404("Invoice not found.")
    back = (
        reverse("task-detail", kwargs={"task_id": invoice.task_id}) if invoice.task_id
        else reverse("billing-home")
    )
    return_url = request.build_absolute_uri(reverse("plan-checkout-return"))
    try:
        checkout_url = billing.start_invoice_checkout(
            invoice=invoice, membership=request.membership, return_url=return_url
        )
    except (ValidationFailure, PermissionFailure) as exc:
        messages.error(request, exc.message)
        return redirect(back)
    return redirect(checkout_url)


@client_required
@require_GET
def plan_checkout_return(request):
    """Where Paystack sends the client back. It shows status; it never activates anything."""
    reference = (request.GET.get("reference") or request.GET.get("trxref") or "")[:64]
    attempt = (
        PaymentAttempt.objects.select_related("invoice", "invoice__plan", "invoice__task")
        .filter(reference=reference, invoice__organization=request.organization)
        .first()
    )
    if attempt is None:
        raise Http404("Payment not found.")
    return render(
        request,
        "app/checkout_return.html",
        {
            "attempt": attempt,
            "invoice": attempt.invoice,
            "paid": attempt.invoice.status == "paid",
            "subscription": billing.active_subscription(request.organization),
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
