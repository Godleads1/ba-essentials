"""Third-party fees: prepared with evidence, approved by a second person, paid through
Paystack and confirmed by the server, then paid out with receipts (Terms 5.3)."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from django.conf import settings
from django.test import override_settings

from apps.audit.models import AuditEvent
from apps.billing import fees
from apps.billing import services as billing
from apps.billing.models import (
    FeeRequest,
    FeeRequestStatus,
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
)
from apps.communications.models import Notification
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.tasks.models import Task, TaskStatus
from apps.tasks.transitions import Action, transition

from .conftest import login_client, make_user, pdf_upload

CAC = 3_500_000  # ₦35,000
STAMP = 750_000  # ₦7,500


# ---------- the Paystack charge is added so the full fees reach the organisations ----------

def test_gross_up_matches_the_client_preview():
    assert fees.gross_up(CAC + STAMP) == 4_324_900  # ₦43,249, so ₦749 processing


@pytest.mark.parametrize("net", [1, 99_99, 150_000, 246_000, 246_300, 250_000, 1_000_000,
                                 4_250_000, 13_000_000, 13_300_000, 50_000_000, 900_000_000])
def test_gross_up_always_leaves_the_full_fees_and_is_the_smallest_total(net):
    total = fees.gross_up(net)
    assert total - fees.paystack_charge(total) >= net
    assert total % 100 == 0  # whole naira, as the client sees it
    assert (total - 100) - fees.paystack_charge(total - 100) < net


def test_paystack_charge_is_capped_and_waives_the_flat_fee_on_small_payments():
    assert fees.paystack_charge(200_000) == 3_000  # ₦2,000 → 1.5% only
    assert fees.paystack_charge(1_000_000) == 25_000  # ₦10,000 → ₦150 + ₦100
    assert fees.paystack_charge(100_000_000) == 200_000  # capped at ₦2,000
    assert fees.gross_up(100_000_000) == 100_200_000


# ---------- helpers ----------

@pytest.fixture
def approver2(db):
    from apps.accounts.models import StaffRole

    return make_user("approver2@example.test", staff_role=StaffRole.QUOTE_APPROVER, mfa=True, is_staff=True)


def _draft_with_lines(task, preparer):
    draft = fees.draft_for(task, preparer)
    fees.add_line(draft, preparer, payee="Corporate Affairs Commission",
                  description="Incorporation filing fee", amount_minor=CAC,
                  evidence=pdf_upload("CAC_Fee_Schedule.pdf"))
    fees.add_line(draft, preparer, payee="Federal Inland Revenue Service",
                  description="Stamp duty on share capital", amount_minor=STAMP,
                  evidence=pdf_upload("Remita_RRR_Invoice.pdf"))
    return draft


@pytest.fixture
def sent_fees(scoping_task, ops_user, finance):
    draft = _draft_with_lines(scoping_task, ops_user)
    fees.submit_for_approval(draft, ops_user)
    return fees.approve(draft, finance)


def _webhook(client, payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(settings.PAYSTACK_WEBHOOK_SECRET.encode(), raw, hashlib.sha512).hexdigest()
    return client.post("/webhooks/paystack/", raw, content_type="application/json",
                       HTTP_X_PAYSTACK_SIGNATURE=signature)


def _pay(owner, fee_request, fake_paystack, *, provider_fee=None, card_country="NG"):
    response = login_client(owner).post(f"/app/billing/invoices/{fee_request.invoice_id}/pay/")
    assert response.status_code == 302 and response["Location"].startswith("https://checkout.paystack.com/")
    attempt = PaymentAttempt.objects.get(invoice_id=fee_request.invoice_id)
    fake_paystack.confirm(attempt.reference, attempt.amount_minor)
    fake_paystack.verified[attempt.reference]["fees"] = provider_fee
    fake_paystack.verified[attempt.reference]["authorization"] = {"country_code": card_country}
    from django.test import Client

    _webhook(Client(), {"event": "charge.success", "data": {
        "id": 9001, "reference": attempt.reference, "amount": attempt.amount_minor,
        "currency": "NGN", "status": "success"}})
    fee_request.refresh_from_db()
    return attempt


# ---------- preparing and approving ----------

def test_only_staff_with_fees_prepare_can_add_fees(scoping_task, specialist, reviewer, owner):
    for user in (specialist, reviewer, owner):
        with pytest.raises(PermissionFailure):
            fees.draft_for(scoping_task, user)


def test_fees_need_evidence_and_a_real_amount(scoping_task, ops_user):
    draft = fees.draft_for(scoping_task, ops_user)
    with pytest.raises(ValidationFailure):
        fees.add_line(draft, ops_user, payee="CAC", description="Filing", amount_minor=CAC, evidence=None)
    for bad in (0, -5, None):
        with pytest.raises(ValidationFailure):
            fees.add_line(draft, ops_user, payee="CAC", description="Filing", amount_minor=bad,
                          evidence=pdf_upload())


def test_fees_cant_be_raised_before_scoping(submitted_task, ops_user):
    with pytest.raises(ValidationFailure):
        fees.draft_for(submitted_task, ops_user)


def test_the_preparer_cannot_approve_their_own_fees(scoping_task, quote_approver, approver2):
    draft = _draft_with_lines(scoping_task, quote_approver)
    fees.submit_for_approval(draft, quote_approver)
    with pytest.raises(ValidationFailure, match="Someone other than"):
        fees.approve(draft, quote_approver)
    approved = fees.approve(draft, approver2)
    assert approved.status == FeeRequestStatus.SENT


def test_operations_cannot_approve_fees(scoping_task, ops_user):
    draft = _draft_with_lines(scoping_task, ops_user)
    fees.submit_for_approval(draft, ops_user)
    with pytest.raises(PermissionFailure):
        fees.approve(draft, ops_user)


def test_approval_fixes_the_total_issues_an_invoice_and_tells_the_client(sent_fees, owner):
    assert (sent_fees.net_minor, sent_fees.processing_minor, sent_fees.total_minor) == (4_250_000, 74_900, 4_324_900)
    invoice = sent_fees.invoice
    assert invoice.amount_minor == 4_324_900 and invoice.status == InvoiceStatus.ISSUED
    assert Notification.objects.filter(user=owner, task=sent_fees.task).exists()
    assert AuditEvent.objects.filter(action="fees.approved", resource_id=str(sent_fees.pk)).exists()


def test_approved_fees_are_locked(sent_fees, ops_user):
    line = sent_fees.lines.first()
    with pytest.raises(ValidationFailure):
        fees.remove_line(line, ops_user)
    with pytest.raises(ValidationFailure):
        fees.add_line(sent_fees, ops_user, payee="X", description="Extra", amount_minor=100, evidence=pdf_upload())


# ---------- what the client sees ----------

def test_client_sees_fees_only_after_approval(scoping_task, ops_user, finance, owner, client):
    draft = _draft_with_lines(scoping_task, ops_user)
    evidence = draft.lines.first().evidence
    url = _client_task_url(scoping_task)
    assert "Corporate Affairs Commission" not in client.get(url).content.decode()
    assert client.get(f"/app/files/{evidence.pk}/download/").status_code in (403, 404)

    fees.submit_for_approval(draft, ops_user)
    fees.approve(draft, finance)
    body = client.get(url).content.decode()
    assert "Corporate Affairs Commission" in body and "43,249" in body
    assert client.get(f"/app/files/{evidence.pk}/download/").status_code == 200


def test_another_business_cannot_see_the_fee_evidence(sent_fees, other_client):
    evidence = sent_fees.lines.first().evidence
    assert other_client.get(f"/app/files/{evidence.pk}/download/").status_code in (403, 404)


def _client_task_url(task):
    from django.urls import reverse

    return reverse("task-detail", kwargs={"task_id": task.pk})


# ---------- paying ----------

@override_settings(PAYMENTS_ENABLED=True)
def test_paystack_confirmed_payment_marks_the_fees_paid(sent_fees, owner, fake_paystack):
    task_status = Task.objects.get(pk=sent_fees.task_id).status
    attempt = _pay(owner, sent_fees, fake_paystack, provider_fee=74_874)
    assert attempt.amount_minor == 4_324_900  # from the stored invoice, never the browser
    assert sent_fees.status == FeeRequestStatus.PAID and sent_fees.shortfall_minor == 0
    assert Invoice.objects.get(pk=sent_fees.invoice_id).status == InvoiceStatus.PAID
    # Paying fees doesn't move the request or pay for the work itself.
    assert Task.objects.get(pk=sent_fees.task_id).status == task_status
    assert not billing.eligibility(sent_fees.task).allowed


@override_settings(PAYMENTS_ENABLED=True)
def test_a_foreign_card_shortfall_is_recorded_for_finance(sent_fees, owner, fake_paystack):
    _pay(owner, sent_fees, fake_paystack, provider_fee=178_700, card_country="GB")
    assert sent_fees.status == FeeRequestStatus.PAID
    assert sent_fees.shortfall_minor == 178_700 - 74_900 and sent_fees.card_country == "GB"
    assert AuditEvent.objects.filter(action="fees.processing_shortfall").exists()


@override_settings(PAYMENTS_ENABLED=True)
def test_a_payment_paystack_does_not_confirm_leaves_the_fees_unpaid(sent_fees, owner, fake_paystack):
    login_client(owner).post(f"/app/billing/invoices/{sent_fees.invoice_id}/pay/")
    attempt = PaymentAttempt.objects.get(invoice_id=sent_fees.invoice_id)
    fake_paystack.confirm(attempt.reference, attempt.amount_minor, status="failed")
    from django.test import Client

    _webhook(Client(), {"event": "charge.success", "data": {
        "id": 9002, "reference": attempt.reference, "amount": attempt.amount_minor,
        "currency": "NGN", "status": "success"}})
    sent_fees.refresh_from_db()
    assert sent_fees.status == FeeRequestStatus.SENT


@override_settings(PAYMENTS_ENABLED=True)
def test_only_the_business_owner_can_pay(sent_fees, member):
    response = login_client(member).post(f"/app/billing/invoices/{sent_fees.invoice_id}/pay/")
    assert response.status_code == 302 and "checkout.paystack.com" not in response["Location"]
    assert not PaymentAttempt.objects.filter(invoice_id=sent_fees.invoice_id).exists()


@override_settings(PAYMENTS_ENABLED=False)
def test_no_checkout_while_payments_are_switched_off(sent_fees, owner):
    response = login_client(owner).post(f"/app/billing/invoices/{sent_fees.invoice_id}/pay/")
    assert "checkout.paystack.com" not in response["Location"]


@override_settings(PAYMENTS_ENABLED=True)
def test_another_business_cannot_pay_or_see_the_invoice(sent_fees, other_client):
    assert other_client.post(f"/app/billing/invoices/{sent_fees.invoice_id}/pay/").status_code == 404


# ---------- nothing is released or paid out before the client pays ----------

def test_release_is_blocked_while_fees_are_open(sent_fees, ops_user, finance):
    Task.objects.filter(pk=sent_fees.task_id).update(status=TaskStatus.APPROVED_FOR_RELEASE)
    task = Task.objects.get(pk=sent_fees.task_id)
    with pytest.raises(ValidationFailure, match="Third-party fees"):
        transition(task=task, action=Action.RELEASE, actor=ops_user)
    fees.withdraw(sent_fees, finance, "Registry waived the fee")
    assert Invoice.objects.get(pk=sent_fees.invoice_id).status == InvoiceStatus.VOID
    transition(task=task, action=Action.RELEASE, actor=ops_user)


def test_organisations_are_paid_only_after_the_client_pays(sent_fees, finance):
    line = sent_fees.lines.first()
    with pytest.raises(ValidationFailure, match="after the client has paid"):
        fees.record_payee_payment(line, finance, reference="CAC-RCPT-1", receipt=pdf_upload("receipt.pdf"))


@override_settings(PAYMENTS_ENABLED=True)
def test_paying_out_every_fee_with_a_receipt_settles_them(sent_fees, owner, finance, ops_user, fake_paystack, client):
    _pay(owner, sent_fees, fake_paystack)
    with pytest.raises(PermissionFailure):
        fees.record_payee_payment(sent_fees.lines.first(), ops_user, reference="R-1", receipt=pdf_upload())
    for index, line in enumerate(sent_fees.lines.all()):
        fees.record_payee_payment(line, finance, reference=f"RCPT-{index}", receipt=pdf_upload(f"r{index}.pdf"))
    sent_fees.refresh_from_db()
    assert sent_fees.status == FeeRequestStatus.SETTLED
    receipt = sent_fees.lines.first().receipt
    assert client.get(f"/app/files/{receipt.pk}/download/").status_code == 200
    with pytest.raises(ValidationFailure):
        fees.withdraw(sent_fees, finance, "too late")


def test_finance_recording_a_bank_transfer_also_marks_fees_paid(sent_fees, finance):
    invoice = sent_fees.invoice
    billing.verify_payment(invoice, finance, reference="TRF-778899", amount_minor=invoice.amount_minor,
                           currency="NGN")
    sent_fees.refresh_from_db()
    assert sent_fees.status == FeeRequestStatus.PAID


def test_staff_page_shows_fees_and_blocks_self_approval(scoping_task, quote_approver, approver2):
    draft = _draft_with_lines(scoping_task, quote_approver)
    fees.submit_for_approval(draft, quote_approver)
    url = f"/staff/tasks/{scoping_task.pk}/"
    body = login_client(quote_approver).get(url).content.decode()
    assert "Waiting for someone else to approve" in body
    response = login_client(approver2).post(f"/staff/fees/{draft.pk}/approve/")
    assert response.status_code == 302
    draft.refresh_from_db()
    assert draft.status == FeeRequestStatus.SENT


def test_staff_add_fee_form_works_end_to_end(scoping_task, ops_user):
    c = login_client(ops_user)
    response = c.post(f"/staff/tasks/{scoping_task.pk}/fees/lines/", {
        "payee": "Corporate Affairs Commission", "description": "Filing fee", "amount": "35,000",
        "evidence": pdf_upload("CAC_Fee_Schedule.pdf"),
    })
    assert response.status_code == 302
    line = FeeRequest.objects.get(task=scoping_task).lines.get()
    assert line.amount_minor == CAC
