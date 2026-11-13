from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import staff_permission_required
from apps.accounts.permissions import (
    FEES_APPROVE,
    FEES_PREPARE,
    FEES_SETTLE,
    FINANCE_VIEW,
    FINANCE_VERIFY_PAYMENT,
)
from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error

from . import fees, services
from .models import Invoice, InvoiceStatus, PaymentAttempt, PaymentStatus


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
    refunds_due = (
        PaymentAttempt.objects.filter(status=PaymentStatus.REFUND_DUE)
        .select_related("invoice", "invoice__organization")
        .order_by("-updated_at")[:50]
    )
    return render(
        request,
        "staff/finance.html",
        {"issued": issued, "recent_paid": recent_paid, "refunds_due": refunds_due},
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


# ---------- third-party fees ----------

def _staff_task(request, task_id):
    from apps.tasks.access import can_view_task
    from apps.tasks.models import Task

    task = Task.objects.filter(pk=task_id).select_related("organization").first()
    if task is None or not can_view_task(request.user, task):
        raise Http404("Request not found")
    return task


def _staff_fee_request(request, fee_id):
    from .models import FeeRequest

    fee_request = FeeRequest.objects.filter(pk=fee_id).select_related("task").first()
    if fee_request is None:
        raise Http404("Fees not found")
    _staff_task(request, fee_request.task_id)
    return fee_request


def _staff_fee_line(request, line_id):
    from .models import FeeLine

    line = FeeLine.objects.filter(pk=line_id).select_related("fee_request__task").first()
    if line is None:
        raise Http404("Fee not found")
    _staff_task(request, line.fee_request.task_id)
    return line


def _back(task_id):
    from django.urls import reverse

    return reverse("staff-task-detail", kwargs={"task_id": task_id}) + "#fees"


def _fee_action(request, task_id, func, success: str):
    try:
        func()
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to=_back(task_id))
    messages.success(request, success)
    return redirect(_back(task_id))


@staff_permission_required(FEES_PREPARE)
@require_POST
def fee_add_line(request, task_id):
    task = _staff_task(request, task_id)
    amount_minor = _parse_minor(request.POST.get("amount", ""))

    def run():
        if not amount_minor:
            from apps.core.exceptions import ValidationFailure

            raise ValidationFailure("Enter the fee amount.")
        draft = fees.draft_for(task, request.user)
        fees.add_line(
            draft,
            request.user,
            payee=request.POST.get("payee", ""),
            description=request.POST.get("description", ""),
            amount_minor=amount_minor,
            evidence=request.FILES.get("evidence"),
        )

    return _fee_action(request, task.pk, run, "Fee added to the draft.")


@staff_permission_required(FEES_PREPARE)
@require_POST
def fee_remove_line(request, line_id):
    line = _staff_fee_line(request, line_id)
    return _fee_action(
        request, line.fee_request.task_id, lambda: fees.remove_line(line, request.user), "Fee removed."
    )


@staff_permission_required(FEES_PREPARE)
@require_POST
def fee_submit(request, fee_id):
    fee_request = _staff_fee_request(request, fee_id)
    return _fee_action(
        request,
        fee_request.task_id,
        lambda: fees.submit_for_approval(fee_request, request.user),
        "Sent for a second person to approve.",
    )


@staff_permission_required(FEES_APPROVE)
@require_POST
def fee_approve(request, fee_id):
    fee_request = _staff_fee_request(request, fee_id)
    return _fee_action(
        request,
        fee_request.task_id,
        lambda: fees.approve(fee_request, request.user),
        "Approved. The client has been asked to pay.",
    )


@staff_permission_required(FEES_APPROVE)
@require_POST
def fee_return(request, fee_id):
    fee_request = _staff_fee_request(request, fee_id)
    return _fee_action(
        request,
        fee_request.task_id,
        lambda: fees.return_to_draft(fee_request, request.user, request.POST.get("reason", "")),
        "Returned to draft.",
    )


@staff_permission_required(FEES_APPROVE)
@require_POST
def fee_withdraw(request, fee_id):
    fee_request = _staff_fee_request(request, fee_id)
    return _fee_action(
        request,
        fee_request.task_id,
        lambda: fees.withdraw(fee_request, request.user, request.POST.get("reason", "")),
        "Fees withdrawn.",
    )


@staff_permission_required(FEES_SETTLE)
@require_POST
def fee_paid_out(request, line_id):
    line = _staff_fee_line(request, line_id)
    return _fee_action(
        request,
        line.fee_request.task_id,
        lambda: fees.record_payee_payment(
            line,
            request.user,
            reference=request.POST.get("reference", ""),
            receipt=request.FILES.get("receipt"),
        ),
        f"Payment to {line.payee} recorded.",
    )
