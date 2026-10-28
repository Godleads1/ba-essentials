from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from django.conf import settings
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import (
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
    PaymentStatus,
    UsageKind,
    UsageLedger,
)
from apps.core.exceptions import (
    PaymentRequired,
    PermissionFailure,
    ValidationFailure,
)
from apps.files.models import FilePurpose, PrivateFile, ScanStatus
from apps.quotes import services as quotes
from apps.quotes.models import QuoteAcceptance, QuoteStatus
from apps.reviews import services as reviews
from apps.reviews.models import Deliverable, DeliverableVersion, DeliverableVersionStatus
from apps.tasks import services as task_services
from apps.tasks.models import Task, TaskStatus


def _send_quote(task, actor, *, amount_minor=150_000, revision_allowance=1):
    version = quotes.create_version(
        task,
        actor,
        scope_summary="Refresh the logo and prepare print-ready exports.",
        exclusions="Copywriting and photography are not included.",
        amount_minor=amount_minor,
        turnaround_days=5,
        revision_allowance=revision_allowance,
    )
    return quotes.send_version(task, version, actor)


def _signed_webhook(payload: dict) -> tuple[bytes, str]:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        settings.PAYSTACK_WEBHOOK_SECRET.encode("utf-8"), raw, hashlib.sha512
    ).hexdigest()
    return raw, signature


def _charge_payload(reference: str, amount_minor: int, event_id: int) -> dict:
    return {
        "event": "charge.success",
        "data": {
            "id": event_id,
            "reference": reference,
            "amount": amount_minor,
            "currency": "NGN",
            "status": "success",
        },
    }


def test_rejected_quote_leads_to_numbered_replacement(
    scoping_task, quote_approver, owner
):
    first = _send_quote(scoping_task, quote_approver)
    scoping_task.refresh_from_db()

    quotes.reject_quote(
        task=scoping_task, version=first, actor=owner, reason="Too expensive"
    )
    scoping_task.refresh_from_db()
    assert scoping_task.status == TaskStatus.SCOPING

    second = _send_quote(scoping_task, quote_approver, amount_minor=120_000)

    first.refresh_from_db()
    assert (first.version_number, second.version_number) == (1, 2)
    assert first.status == QuoteStatus.REJECTED
    assert second.status == QuoteStatus.SENT


def test_quote_cannot_be_sent_without_scoping(scoping_task, quote_approver):
    version = quotes.create_version(
        scoping_task,
        quote_approver,
        scope_summary="Scope",
        exclusions="",
        amount_minor=1000,
        turnaround_days=2,
        revision_allowance=0,
    )
    scoping_task.status = TaskStatus.IN_PROGRESS
    scoping_task.save(update_fields=["status"])

    with pytest.raises(Exception) as excinfo:
        quotes.send_version(scoping_task, version, quote_approver)
    assert "not in scoping" in str(excinfo.value).lower()


def test_non_owner_member_cannot_accept_quote(scoping_task, quote_approver, member):
    version = _send_quote(scoping_task, quote_approver)

    with pytest.raises(PermissionFailure):
        quotes.accept_quote(
            task=scoping_task,
            version=version,
            actor=member,
            idempotency_key="member-attempt",
        )

    scoping_task.refresh_from_db()
    version.refresh_from_db()
    assert scoping_task.status == TaskStatus.AWAITING_CLIENT_APPROVAL
    assert version.status == QuoteStatus.SENT
    assert not Invoice.objects.filter(task=scoping_task).exists()


def test_accepting_a_quote_creates_one_invoice_and_replays_idempotently(
    scoping_task, quote_approver, owner
):
    version = _send_quote(scoping_task, quote_approver)

    outcome = quotes.accept_quote(
        task=scoping_task, version=version, actor=owner, idempotency_key="accept-1"
    )
    assert outcome.covered_by_allowance is False
    assert outcome.invoice is not None
    assert outcome.replayed is False

    replay = quotes.accept_quote(
        task=scoping_task, version=version, actor=owner, idempotency_key="accept-1"
    )
    assert replay.replayed is True
    assert replay.invoice.pk == outcome.invoice.pk

    scoping_task.refresh_from_db()
    version.refresh_from_db()
    assert scoping_task.status == TaskStatus.AWAITING_PAYMENT
    assert version.status == QuoteStatus.ACCEPTED
    assert Invoice.objects.filter(task=scoping_task).count() == 1
    assert QuoteAcceptance.objects.filter(quote_version=version).count() == 1


def test_expired_quote_cannot_be_accepted(scoping_task, quote_approver, owner):
    version = _send_quote(scoping_task, quote_approver)
    version.expires_at = timezone.now() - timezone.timedelta(seconds=5)
    version.save(update_fields=["expires_at"])

    with pytest.raises(ValidationFailure):
        quotes.accept_quote(
            task=scoping_task,
            version=version,
            actor=owner,
            idempotency_key="expired",
        )

    version.refresh_from_db()
    scoping_task.refresh_from_db()
    assert version.status == QuoteStatus.EXPIRED
    assert scoping_task.status == TaskStatus.AWAITING_CLIENT_APPROVAL


def test_zero_quote_is_covered_without_an_invoice(scoping_task, quote_approver, owner):
    version = _send_quote(scoping_task, quote_approver, amount_minor=0)

    outcome = quotes.accept_quote(
        task=scoping_task, version=version, actor=owner, idempotency_key="zero"
    )

    assert outcome.covered_by_allowance is True
    assert outcome.invoice is None
    assert not Invoice.objects.filter(task=scoping_task).exists()
    scoping_task.refresh_from_db()
    assert scoping_task.status == TaskStatus.READY_FOR_ASSIGNMENT


def test_plan_allowance_covers_quote(
    scoping_task, quote_approver, owner, subscription, organization
):
    assert billing.allowance_remaining(organization) == 2
    version = _send_quote(scoping_task, quote_approver)

    outcome = quotes.accept_quote(
        task=scoping_task, version=version, actor=owner, idempotency_key="allowance"
    )

    assert outcome.covered_by_allowance is True
    assert outcome.invoice is None
    assert UsageLedger.objects.filter(
        task=scoping_task, kind=UsageKind.ALLOWANCE
    ).count() == 1
    assert billing.allowance_remaining(organization) == 1
    scoping_task.refresh_from_db()
    assert scoping_task.status == TaskStatus.READY_FOR_ASSIGNMENT


def test_cancelling_covered_request_releases_allowance(
    scoping_task, quote_approver, owner, subscription, organization
):
    version = _send_quote(scoping_task, quote_approver)
    quotes.accept_quote(
        task=scoping_task, version=version, actor=owner, idempotency_key="release-1"
    )
    scoping_task.refresh_from_db()

    task_services.cancel_by_client(scoping_task, owner, "Not needed after all")

    assert not UsageLedger.objects.filter(task=scoping_task, kind=UsageKind.ALLOWANCE).exists()
    assert billing.allowance_remaining(organization) == 2


def test_finance_can_only_verify_the_exact_amount(
    scoping_task, quote_approver, owner, finance
):
    version = _send_quote(scoping_task, quote_approver)
    outcome = quotes.accept_quote(
        task=scoping_task, version=version, actor=owner, idempotency_key="pay"
    )
    invoice = outcome.invoice

    with pytest.raises(ValidationFailure):
        billing.verify_payment(
            invoice,
            finance,
            reference="BANK-REF-1",
            amount_minor=invoice.amount_minor - 1,
            currency=invoice.currency,
        )

    invoice.refresh_from_db()
    scoping_task.refresh_from_db()
    assert invoice.status == InvoiceStatus.ISSUED
    assert scoping_task.status == TaskStatus.AWAITING_PAYMENT

    billing.verify_payment(
        invoice,
        finance,
        reference="BANK-REF-1",
        amount_minor=invoice.amount_minor,
        currency=invoice.currency,
    )

    invoice.refresh_from_db()
    scoping_task.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID
    assert scoping_task.status == TaskStatus.READY_FOR_ASSIGNMENT


def test_signed_webhook_marks_invoice_paid_once(
    scoping_task, quote_approver, owner
):
    version = _send_quote(scoping_task, quote_approver)
    outcome = quotes.accept_quote(
        task=scoping_task, version=version, actor=owner, idempotency_key="webhook-1"
    )
    invoice = outcome.invoice
    PaymentAttempt.objects.create(
        invoice=invoice,
        provider="paystack",
        reference="PS-REF-1",
        amount_minor=invoice.amount_minor,
        currency=invoice.currency,
        status=PaymentStatus.INITIATED,
    )
    raw, signature = _signed_webhook(
        _charge_payload("PS-REF-1", invoice.amount_minor, 900001)
    )

    event, duplicate = billing.process_webhook(raw_body=raw, signature=signature)
    assert duplicate is False
    assert event.outcome == "paid"

    invoice.refresh_from_db()
    scoping_task.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID
    assert scoping_task.status == TaskStatus.READY_FOR_ASSIGNMENT

    replay, duplicate = billing.process_webhook(raw_body=raw, signature=signature)
    assert duplicate is True
    assert replay.pk == event.pk
    assert Invoice.objects.filter(task=scoping_task).count() == 1


def test_webhook_rejects_bad_signature_and_amount_mismatch(
    scoping_task, quote_approver, owner
):
    version = _send_quote(scoping_task, quote_approver)
    invoice = quotes.accept_quote(
        task=scoping_task, version=version, actor=owner, idempotency_key="webhook-2"
    ).invoice
    PaymentAttempt.objects.create(
        invoice=invoice,
        provider="paystack",
        reference="PS-REF-2",
        amount_minor=invoice.amount_minor,
        currency=invoice.currency,
        status=PaymentStatus.INITIATED,
    )

    raw, signature = _signed_webhook(
        _charge_payload("PS-REF-2", invoice.amount_minor, 900002)
    )
    with pytest.raises(PermissionFailure):
        billing.process_webhook(raw_body=raw, signature=signature[::-1])

    raw, signature = _signed_webhook(
        _charge_payload("PS-REF-2", invoice.amount_minor - 500, 900003)
    )
    event, duplicate = billing.process_webhook(raw_body=raw, signature=signature)

    assert duplicate is False
    assert event.outcome == "amount_mismatch"
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.ISSUED


def _approved_deliverable(task, org, submitted_by):
    private = PrivateFile.objects.create(
        organization=org,
        purpose=FilePurpose.DELIVERABLE,
        storage_key=f"{org.pk}/{uuid.uuid4().hex}.pdf",
        original_name="final.pdf",
        size_bytes=10,
        content_type="application/pdf",
        sha256="0" * 64,
        scan_status=ScanStatus.CLEAN,
    )
    deliverable, _ = Deliverable.objects.get_or_create(task=task)
    version = DeliverableVersion.objects.create(
        deliverable=deliverable,
        version_number=1,
        file=private,
        submitted_by=submitted_by,
        status=DeliverableVersionStatus.APPROVED,
    )
    return version


def test_release_is_blocked_until_payment_is_evidenced(
    submitted_task, ops_user, specialist, organization
):
    Task.objects.filter(pk=submitted_task.pk).update(
        status=TaskStatus.APPROVED_FOR_RELEASE
    )
    submitted_task.refresh_from_db()
    version = _approved_deliverable(submitted_task, organization, specialist)
    invoice = Invoice.objects.create(
        number="INV-TEST-1",
        organization=organization,
        task=submitted_task,
        amount_minor=500_000,
        status=InvoiceStatus.ISSUED,
    )

    with pytest.raises(PaymentRequired):
        reviews.release(submitted_task, version, ops_user)

    version.refresh_from_db()
    submitted_task.refresh_from_db()
    assert version.status == DeliverableVersionStatus.APPROVED
    assert submitted_task.status == TaskStatus.APPROVED_FOR_RELEASE

    invoice.status = InvoiceStatus.PAID
    invoice.save(update_fields=["status"])

    reviews.release(submitted_task, version, ops_user)

    version.refresh_from_db()
    submitted_task.refresh_from_db()
    assert version.status == DeliverableVersionStatus.RELEASED
    assert submitted_task.status == TaskStatus.DELIVERED
