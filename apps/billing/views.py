from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.billing import plan_rules, pricing, renewals
from apps.billing import services as billing
from apps.billing.models import BillingSettings, Invoice, PaymentAttempt, Plan, UsageLedger
from apps.catalogue.models import ServiceCategory
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.organizations.access import client_required


def _renewal_price(subscription):
    if subscription is None:
        return None
    try:
        return renewals.renewal_price(subscription)[1]
    except ValidationFailure:
        return None


@client_required
@require_GET
def plan_home(request):
    from apps.billing.models import InvoiceStatus, Subscription, SubscriptionStatus

    subscription = billing.active_subscription(request.organization)
    paused = Subscription.objects.filter(
        organization=request.organization, status=SubscriptionStatus.PAST_DUE
    ).select_related("plan").first()
    renewal_invoice = Invoice.objects.filter(
        organization=request.organization, status=InvoiceStatus.ISSUED, renews_period_end__isnull=False
    ).order_by("-issued_at").first()
    usage = UsageLedger.objects.filter(
        organization=request.organization
    ).select_related("task")
    if subscription is not None:
        usage = usage.filter(period_start=billing.task_window_start(subscription))
    region = request.organization.region
    plans = []
    rate_error = ""
    if subscription is None:
        rate = None
        for plan in Plan.objects.filter(is_active=True, price_minor__gt=0).order_by("rank", "price_minor"):
            try:
                monthly = pricing.plan_price(plan, region, "month", rate=rate)
                yearly = pricing.plan_price(plan, region, "year", rate=monthly.rate)
            except ValidationFailure as exc:
                rate_error = exc.message
                continue
            rate = monthly.rate
            plans.append((plan, monthly, yearly))
    legal_fee, legal_currency = pricing.extra_legal_fee(region)
    from apps.tasks.dashboard import plan_usage

    entries = list(usage.select_related("task__service_version__definition__category").order_by("-created_at")[:100])
    by_category: dict[str, int] = {}
    for entry in entries:
        if entry.task_id:
            name = entry.task.service_version.definition.category.name
            by_category[name] = by_category.get(name, 0) + entry.units
    spent = sum(by_category.values()) or 1
    gauge = plan_usage(request.organization, subscription)
    return render(
        request,
        "banda/app/plan.html",
        {
            "gauge": gauge,
            "used_pct": min(100, gauge["percent"]) if gauge else 0,
            "days_left": max(0, (subscription.period_end - timezone.now()).days) if subscription else None,
            "by_category": [(name, n, round(n / spent * 100)) for name, n in
                            sorted(by_category.items(), key=lambda kv: -kv[1])],
            "subscription": subscription,
            "allowance_remaining": billing.allowance_remaining(request.organization),
            "usage": entries,
            "plans": plans,
            "rate_error": rate_error,
            "region": region,
            "provider_name": "Paystack" if region == "ng" else "Flutterwave",
            "yearly_discount": BillingSettings.current().yearly_discount_pct,
            "extra_legal_fee": legal_fee,
            "extra_legal_currency": legal_currency,
            "can_buy": request.membership.role == "owner",
            "upgrades": billing.upgrade_options(request.organization),
            "legal_remaining": plan_rules.legal_reviews_remaining(request.organization),
            "legal_window_start": plan_rules.legal_window_start(subscription) if subscription else None,
            "categories": [
                (category, plan_rules.category_access(request.organization, category))
                for category in ServiceCategory.objects.filter(is_active=True)
            ],
            "payments_enabled": settings.PAYMENTS_ENABLED,
            "paused": paused,
            "renewal_invoice": renewal_invoice,
            "saved_card": renewals.saved_card(request.organization.pk),
            "downgrades": (
                Plan.objects.filter(is_active=True, price_minor__gt=0, rank__lt=subscription.plan.rank).order_by("rank")
                if subscription else []
            ),
            "renewal_price": _renewal_price(subscription),
            "paused_ends": paused.paused_at + renewals.END_AFTER_PAUSE if paused and paused.paused_at else None,
            "reminder_on": subscription.period_end - renewals.REMINDER_LEAD if subscription else None,
            "pause_on": renewals.grace_end(subscription.period_end) if subscription else None,
            "now": timezone.now(),
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
            interval=request.POST.get("interval", "month"),
        )
    except (ValidationFailure, PermissionFailure) as exc:
        messages.error(request, exc.message)
        return redirect("plan-home")
    # Both clients only return an https address on the provider's own checkout host.
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


def _plan_change(request, action, success: str):
    try:
        action()
    except (ValidationFailure, PermissionFailure) as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, success)
    return redirect("plan-home")


@client_required
@require_POST
def plan_cancel(request):
    """The client won't renew: reminders stop and the plan runs to the end of the paid period."""
    return _plan_change(
        request, lambda: renewals.cancel_renewal(request.organization, request.membership),
        "Done. We won't send renewal reminders. Your plan stays active until the end of this period, then ends.",
    )


@client_required
@require_POST
def plan_resume(request):
    return _plan_change(
        request, lambda: renewals.undo_cancel(request.organization, request.membership),
        "Done. We'll remind you 3 days before your plan ends, with a link to pay.",
    )


@client_required
@require_POST
def plan_downgrade(request):
    return _plan_change(
        request,
        lambda: renewals.schedule_downgrade(request.organization, request.membership, request.POST.get("plan", "")),
        "Done. The smaller plan starts at your next renewal.",
    )


@client_required
@require_POST
def plan_downgrade_undo(request):
    return _plan_change(
        request, lambda: renewals.undo_downgrade(request.organization, request.membership),
        "You'll stay on your current plan.",
    )


@client_required
@require_POST
def card_remove(request):
    return _plan_change(
        request, lambda: renewals.remove_card(request.organization, request.membership),
        "Card removed. We'll send you a payment link before each renewal instead.",
    )


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
    reference = (
        request.GET.get("reference") or request.GET.get("trxref") or request.GET.get("tx_ref") or ""
    )[:64]
    attempt = (
        PaymentAttempt.objects.select_related("invoice", "invoice__plan", "invoice__task")
        .filter(reference=reference, invoice__organization=request.organization)
        .first()
    )
    if attempt is None:
        raise Http404("Payment not found.")
    return render(
        request,
        "banda/app/checkout_return.html",
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
    """Billing & Invoices: what's owed, what's paid, the invoices, and how the plan renews."""
    from collections import defaultdict

    from django.db.models import Q

    from apps.billing.models import InvoiceStatus
    from apps.core import client_status

    organization = request.organization
    every = Invoice.objects.filter(organization=organization).select_related("task", "plan")
    year_start = timezone.localtime().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    owed, paid = defaultdict(int), defaultdict(int)
    owed_count = 0
    for status, amount, currency, paid_at in every.values_list("status", "amount_minor", "currency", "paid_at"):
        if status == InvoiceStatus.ISSUED:
            owed[currency] += amount
            owed_count += 1
        elif status == InvoiceStatus.PAID and paid_at and paid_at >= year_start:
            paid[currency] += amount

    show = request.GET.get("status", "")
    query = (request.GET.get("q") or "").strip()[:60]
    invoices = every
    if show == "open":
        invoices = invoices.filter(status=InvoiceStatus.ISSUED)
    elif show == "paid":
        invoices = invoices.filter(status=InvoiceStatus.PAID)
    else:
        show = ""
    if query:
        invoices = invoices.filter(Q(number__icontains=query) | Q(task__reference__icontains=query)
                                   | Q(task__title__icontains=query) | Q(plan__name__icontains=query))
    now = timezone.now()
    rows = []
    for invoice in invoices.order_by("-issued_at")[:100]:
        state = invoice.status
        if state == InvoiceStatus.ISSUED and invoice.due_at and invoice.due_at < now:
            state = "overdue"
        rows.append({
            "invoice": invoice,
            "about": _invoice_about(invoice),
            "status": client_status.INVOICE_STATUS.get(state, client_status.INVOICE_STATUS["issued"]),
            "payable": (settings.PAYMENTS_ENABLED and invoice.status == InvoiceStatus.ISSUED
                        and billing.can_pay_invoice(invoice, request.membership)
                        and (invoice.plan_id is None or invoice.renews_period_end is not None)
                        and invoice.currency in ("NGN", "USD")),
        })
    subscription = billing.active_subscription(organization)
    return render(request, "banda/app/billing.html", {
        "rows": rows, "show": show, "query": query, "filtered": bool(show or query),
        "owed": dict(owed), "owed_count": owed_count, "paid": dict(paid), "year": year_start.year,
        "subscription": subscription,
        "allowance_remaining": billing.allowance_remaining(organization),
        "renewal_price": _renewal_price(subscription),
        "saved_card": renewals.saved_card(organization.pk),
        "can_buy": request.membership.role == "owner",
        "payments_enabled": settings.PAYMENTS_ENABLED,
        "provider_name": "Paystack" if organization.region == "ng" else "Flutterwave",
    })


def _invoice_about(invoice) -> str:
    if invoice.task_id:
        return f"{invoice.task.reference} · {invoice.task.title}"
    if invoice.renews_period_end:
        return f"{invoice.plan.name} plan renewal" if invoice.plan_id else "Plan renewal"
    if invoice.upgrade_from_id:
        return f"Upgrade to {invoice.plan.name}" if invoice.plan_id else "Plan upgrade"
    if invoice.plan_id:
        return f"{invoice.plan.name} plan, {'yearly' if invoice.interval == 'year' else 'monthly'}"
    return "Invoice"

