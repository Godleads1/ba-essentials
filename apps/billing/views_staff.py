from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import staff_permission_required
from apps.accounts.permissions import FINANCE_VIEW, FINANCE_VERIFY_PAYMENT
from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error

from . import services
from .models import Invoice, InvoiceStatus


@staff_permission_required(FINANCE_VIEW)
@require_GET
def finance_home(request):
    issued = (
        Invoice.objects.filter(status=InvoiceStatus.ISSUED)
        .select_related("organization", "task")
        .order_by("due_at")[:100]
    )
    recent_paid = (
        Invoice.objects.filter(status=InvoiceStatus.PAID)
        .select_related("organization", "task")
        .order_by("-paid_at")[:20]
    )
    return render(
        request,
        "staff/finance.html",
        {"issued": issued, "recent_paid": recent_paid},
    )


def _parse_minor(raw: str) -> int | None:
    try:
        amount = Decimal(raw.replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None
    if amount < 0:
        return None
    return int((amount * 100).to_integral_value())


@staff_permission_required(FINANCE_VERIFY_PAYMENT)
@require_POST
def verify_payment(request, invoice_id):
    invoice = Invoice.objects.filter(pk=invoice_id).select_related("task").first()
    if invoice is None:
        raise Http404("Invoice not found")
    amount_minor = _parse_minor(request.POST.get("amount", ""))
    if amount_minor is None:
        messages.error(request, "Enter the amount that was received.")
        return redirect("staff-finance")
    try:
        services.verify_payment(
            invoice,
            request.user,
            reference=request.POST.get("reference", ""),
            amount_minor=amount_minor,
            currency=request.POST.get("currency", invoice.currency).strip().upper(),
        )
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to="staff-finance")
    messages.success(request, f"{invoice.number} marked as paid.")
    return redirect("staff-finance")
