"""Third-party fees: registry, government and institution charges found during scoping.

banda collects them at cost and pays them on the client's behalf (Terms 5.3).
Paystack's charge is added on top so the full amount reaches each organisation.

The flow, every step enforced here rather than in the browser:

1. Staff with ``fees.prepare`` add each fee with evidence of the official amount.
2. A different staff member with ``fees.approve`` checks and approves it. Only
   then is the total fixed, an invoice issued and the client told.
3. The client pays through Paystack. Only a payment Paystack itself confirms
   (webhook plus verify) marks the fees paid.
4. Finance pays each organisation and uploads the official receipt.

A request can't be released to the client while any fees are still open.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.permissions import (
    FEES_APPROVE,
    FEES_PREPARE,
    FEES_SETTLE,
    require_perm,
)
from apps.audit import services as audit_service
from apps.core.exceptions import ValidationFailure
from apps.core.sequences import next_value

from .models import (
    FeeLine,
    FeeRequest,
    FeeRequestStatus,
    Invoice,
    InvoiceStatus,
)

# Paystack's local card pricing, in kobo: 1.5% + ₦100, the ₦100 waived under
# ₦2,500, capped at ₦2,000. International cards cost more (3.9% + ₦100); that
# difference is recorded after payment as a shortfall for finance to review.
LOCAL_PERCENT_BP = 150  # basis points
LOCAL_FLAT_MINOR = 10_000
LOCAL_FLAT_FROM_MINOR = 250_000
LOCAL_CAP_MINOR = 200_000

MAX_LINE_MINOR = 100_000_000_00  # ₦100m: anything larger is a typo
MAX_LINES = 20

# Fees can be raised once the work is understood, and until it's delivered.
FEE_STATUSES = (
    "scoping",
    "awaiting_client_approval",
    "awaiting_payment",
    "ready_for_assignment",
    "assigned",
    "in_progress",
    "internal_review",
    "rework_required",
    "approved_for_release",
    "revision_requested",
    "on_hold",
)
OPEN_FEE_STATUSES = (
    FeeRequestStatus.DRAFT,
    FeeRequestStatus.PENDING_APPROVAL,
    FeeRequestStatus.SENT,
)


def paystack_charge(total_minor: int) -> int:
    """What Paystack keeps from a local card payment of ``total_minor`` kobo."""
    percent = -(-total_minor * LOCAL_PERCENT_BP // 10_000)  # rounded up
    flat = LOCAL_FLAT_MINOR if total_minor >= LOCAL_FLAT_FROM_MINOR else 0
    return min(percent + flat, LOCAL_CAP_MINOR)


def gross_up(net_minor: int) -> int:
    """The smallest whole-naira total that leaves at least ``net_minor`` after Paystack's charge."""
    if net_minor <= 0:
        return 0
    keep = 10_000 - LOCAL_PERCENT_BP
    total = -(-net_minor * 10_000 // keep)
    if total >= LOCAL_FLAT_FROM_MINOR:
        total = -(-(net_minor + LOCAL_FLAT_MINOR) * 10_000 // keep)
    if paystack_charge(total) >= LOCAL_CAP_MINOR:
        total = net_minor + LOCAL_CAP_MINOR
    # Clients see whole naira, so round up to the next ₦1 and step on until it covers the fees.
    total = -(-total // 100) * 100
    while total - paystack_charge(total) < net_minor:
        total += 100
    return total


def _audit(action, actor, fee_request, **metadata):
    audit_service.record(
        action=action,
        actor=actor,
        organization_id=fee_request.task.organization_id,
        resource_type="fee_request",
        resource_id=str(fee_request.pk),
        metadata={"task": fee_request.task.reference, **metadata},
    )


def _locked(fee_request: FeeRequest) -> FeeRequest:
    return FeeRequest.objects.select_for_update().select_related("task").get(pk=fee_request.pk)


def open_fee_requests(task):
    return FeeRequest.objects.filter(task=task, status__in=OPEN_FEE_STATUSES)


def has_open_fees(task) -> bool:
    """True while fees are being prepared, approved or are waiting for the client."""
    return open_fee_requests(task).exists()


def draft_for(task, actor) -> FeeRequest:
    """The task's draft fee request, created on first use."""
    require_perm(actor, FEES_PREPARE)
    if task.status not in FEE_STATUSES:
        raise ValidationFailure("Fees can be added once scoping has started and before delivery.")
    with transaction.atomic():
        from apps.tasks.models import Task

        Task.objects.select_for_update().get(pk=task.pk)
        draft = FeeRequest.objects.filter(task=task, status=FeeRequestStatus.DRAFT).first()
        if draft is not None:
            return draft
        if FeeRequest.objects.filter(
            task=task, status__in=(FeeRequestStatus.PENDING_APPROVAL, FeeRequestStatus.SENT)
        ).exists():
            raise ValidationFailure(
                "This request already has fees waiting for approval or payment. "
                "Withdraw them before preparing new ones."
            )
        fee_request = FeeRequest.objects.create(task=task, prepared_by=actor)
        _audit("fees.draft_created", actor, fee_request)
        return fee_request


def add_line(
    fee_request: FeeRequest, actor, *, payee: str, description: str, amount_minor: int, evidence
) -> FeeLine:
    from apps.files import services as file_services
    from apps.files.models import FilePurpose

    require_perm(actor, FEES_PREPARE)
    payee = (payee or "").strip()
    description = (description or "").strip()
    if not 2 <= len(payee) <= 160:
        raise ValidationFailure("Name the organisation being paid.")
    if not 2 <= len(description) <= 255:
        raise ValidationFailure("Say what the fee is for.")
    if amount_minor is None or not 0 < amount_minor <= MAX_LINE_MINOR:
        raise ValidationFailure("Enter the fee amount.")
    if evidence is None:
        raise ValidationFailure("Attach the official fee schedule or invoice as evidence.")

    with transaction.atomic():
        fee_request = _locked(fee_request)
        if fee_request.status != FeeRequestStatus.DRAFT:
            raise ValidationFailure("Fees can only be changed while they're a draft.")
        if fee_request.lines.count() >= MAX_LINES:
            raise ValidationFailure(f"A fee request can have at most {MAX_LINES} lines.")
        stored = file_services.store_upload(
            organization=fee_request.task.organization,
            user=actor,
            uploaded_file=evidence,
            purpose=FilePurpose.FEE_EVIDENCE,
        )
        line = FeeLine.objects.create(
            fee_request=fee_request,
            payee=payee,
            description=description,
            amount_minor=amount_minor,
            evidence=stored,
        )
        _audit("fees.line_added", actor, fee_request, payee=payee, amount_minor=amount_minor)
    return line


def remove_line(line: FeeLine, actor) -> None:
    require_perm(actor, FEES_PREPARE)
    with transaction.atomic():
        fee_request = _locked(line.fee_request)
        if fee_request.status != FeeRequestStatus.DRAFT:
            raise ValidationFailure("Fees can only be changed while they're a draft.")
        payee, amount = line.payee, line.amount_minor
        line.delete()
        _audit("fees.line_removed", actor, fee_request, payee=payee, amount_minor=amount)


def submit_for_approval(fee_request: FeeRequest, actor) -> FeeRequest:
    from apps.files.models import ScanStatus

    require_perm(actor, FEES_PREPARE)
    with transaction.atomic():
        fee_request = _locked(fee_request)
        if fee_request.status != FeeRequestStatus.DRAFT:
            raise ValidationFailure("Only a draft can be sent for approval.")
        lines = list(fee_request.lines.select_related("evidence"))
        if not lines:
            raise ValidationFailure("Add at least one fee first.")
        if any(line.evidence.scan_status != ScanStatus.CLEAN for line in lines):
            raise ValidationFailure(
                "Every evidence file must pass the security scan before approval."
            )
        fee_request.status = FeeRequestStatus.PENDING_APPROVAL
        fee_request.prepared_by = actor
        fee_request.submitted_at = timezone.now()
        fee_request.save(update_fields=["status", "prepared_by", "submitted_at", "updated_at"])
        _audit("fees.submitted", actor, fee_request, lines=len(lines))
    return fee_request


def approve(fee_request: FeeRequest, actor) -> FeeRequest:
    """Second-person approval. Fixes the total, issues the invoice and tells the client."""
    from apps.communications import services as communications
    from apps.communications.models import NotificationKind

    require_perm(actor, FEES_APPROVE)
    with transaction.atomic():
        fee_request = _locked(fee_request)
        if fee_request.status != FeeRequestStatus.PENDING_APPROVAL:
            raise ValidationFailure("These fees aren't waiting for approval.")
        lines = list(fee_request.lines.select_related("evidence"))
        preparers = {fee_request.prepared_by_id} | {line.evidence.uploaded_by_id for line in lines}
        if actor.pk in preparers:
            raise ValidationFailure(
                "Someone other than the person who prepared these fees must approve them."
            )
        if fee_request.task.status not in FEE_STATUSES:
            raise ValidationFailure("This request can no longer take fees.")

        net = sum(line.amount_minor for line in lines)
        total = gross_up(net)
        number = next_value("invoice.number", start=1000)
        now = timezone.now()
        invoice = Invoice.objects.create(
            number=f"INV-{now:%Y}-{number}",
            organization_id=fee_request.task.organization_id,
            task=fee_request.task,
            amount_minor=total,
            currency=fee_request.currency,
            status=InvoiceStatus.ISSUED,
            due_at=now + timedelta(days=3),
        )
        fee_request.net_minor = net
        fee_request.processing_minor = total - net
        fee_request.total_minor = total
        fee_request.invoice = invoice
        fee_request.approved_by = actor
        fee_request.approved_at = now
        fee_request.status = FeeRequestStatus.SENT
        fee_request.save(
            update_fields=[
                "net_minor", "processing_minor", "total_minor", "invoice",
                "approved_by", "approved_at", "status", "updated_at",
            ]
        )
        _audit(
            "fees.approved", actor, fee_request,
            invoice=invoice.number, net_minor=net, total_minor=total,
        )
        communications.notify(
            communications._client_recipients(fee_request.task),
            organization_id=fee_request.task.organization_id,
            kind=NotificationKind.TASK_MESSAGE,
            title=f"Third-party fees to pay on {fee_request.task.reference}",
            task=fee_request.task,
        )
    return fee_request


def return_to_draft(fee_request: FeeRequest, actor, reason: str) -> FeeRequest:
    require_perm(actor, FEES_APPROVE)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationFailure("Say what needs changing.")
    with transaction.atomic():
        fee_request = _locked(fee_request)
        if fee_request.status != FeeRequestStatus.PENDING_APPROVAL:
            raise ValidationFailure("These fees aren't waiting for approval.")
        fee_request.status = FeeRequestStatus.DRAFT
        fee_request.save(update_fields=["status", "updated_at"])
        audit_service.record(
            action="fees.returned",
            actor=actor,
            organization_id=fee_request.task.organization_id,
            resource_type="fee_request",
            resource_id=str(fee_request.pk),
            reason=reason,
        )
    return fee_request


def withdraw(fee_request: FeeRequest, actor, reason: str) -> FeeRequest:
    """Withdraw fees the client hasn't paid. Paid fees are refunded, not withdrawn."""
    require_perm(actor, FEES_APPROVE)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationFailure("Give a short reason for withdrawing these fees.")
    with transaction.atomic():
        fee_request = _locked(fee_request)
        if fee_request.status not in OPEN_FEE_STATUSES:
            raise ValidationFailure("Only unpaid fees can be withdrawn.")
        if fee_request.invoice_id:
            voided = Invoice.objects.filter(
                pk=fee_request.invoice_id, status=InvoiceStatus.ISSUED
            ).update(status=InvoiceStatus.VOID, updated_at=timezone.now())
            if not voided:
                raise ValidationFailure("These fees have already been paid.")
        fee_request.status = FeeRequestStatus.VOID
        fee_request.void_reason = reason[:255]
        fee_request.save(update_fields=["status", "void_reason", "updated_at"])
        audit_service.record(
            action="fees.withdrawn",
            actor=actor,
            organization_id=fee_request.task.organization_id,
            resource_type="fee_request",
            resource_id=str(fee_request.pk),
            reason=reason,
        )
    return fee_request


def mark_paid(invoice: Invoice, *, confirmed: dict | None = None) -> FeeRequest | None:
    """Called inside the transaction that marked ``invoice`` paid. No-op for other invoices."""
    fee_request = (
        FeeRequest.objects.select_for_update()
        .select_related("task")
        .filter(invoice=invoice)
        .first()
    )
    if fee_request is None or fee_request.status != FeeRequestStatus.SENT:
        return fee_request
    confirmed = confirmed or {}
    fee_request.status = FeeRequestStatus.PAID
    fee_request.paid_at = timezone.now()
    provider_fee = confirmed.get("fees")
    if isinstance(provider_fee, int) and provider_fee >= 0:
        fee_request.provider_fee_minor = provider_fee
        fee_request.shortfall_minor = max(0, provider_fee - fee_request.processing_minor)
    fee_request.card_country = str(confirmed.get("card_country") or "")[:2].upper()
    fee_request.save(
        update_fields=[
            "status", "paid_at", "provider_fee_minor", "shortfall_minor", "card_country", "updated_at",
        ]
    )
    _audit("fees.paid", None, fee_request, invoice=invoice.number, total_minor=fee_request.total_minor)
    if fee_request.shortfall_minor:
        # Usually a foreign card. Finance decides whether to absorb it or ask the client.
        _audit(
            "fees.processing_shortfall", None, fee_request,
            shortfall_minor=fee_request.shortfall_minor, card_country=fee_request.card_country,
        )
    return fee_request


def record_payee_payment(line: FeeLine, actor, *, reference: str, receipt) -> FeeLine:
    """Finance has paid the organisation; attach its official receipt."""
    from apps.files import services as file_services
    from apps.files.models import FilePurpose

    require_perm(actor, FEES_SETTLE)
    reference = (reference or "").strip()
    if not 3 <= len(reference) <= 64:
        raise ValidationFailure("Enter the payment or receipt reference from the organisation.")
    if receipt is None:
        raise ValidationFailure("Attach the official receipt.")
    with transaction.atomic():
        fee_request = _locked(line.fee_request)
        if fee_request.status != FeeRequestStatus.PAID:
            raise ValidationFailure("Organisations are paid only after the client has paid.")
        line = FeeLine.objects.select_for_update().get(pk=line.pk)
        if line.paid_to_payee_at is not None:
            raise ValidationFailure("This fee has already been paid.")
        stored = file_services.store_upload(
            organization=fee_request.task.organization,
            user=actor,
            uploaded_file=receipt,
            purpose=FilePurpose.FEE_RECEIPT,
        )
        now = timezone.now()
        line.receipt = stored
        line.payee_reference = reference
        line.paid_to_payee_at = now
        line.paid_to_payee_by = actor
        line.save(update_fields=["receipt", "payee_reference", "paid_to_payee_at", "paid_to_payee_by"])
        _audit("fees.payee_paid", actor, fee_request, payee=line.payee, amount_minor=line.amount_minor)
        if not fee_request.lines.filter(paid_to_payee_at__isnull=True).exists():
            fee_request.status = FeeRequestStatus.SETTLED
            fee_request.settled_at = now
            fee_request.save(update_fields=["status", "settled_at", "updated_at"])
            _audit("fees.settled", actor, fee_request)
    return line
