from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from apps.billing import services as billing
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.files.models import FilePurpose, PrivateFile, ScanStatus
from apps.quotes import services as quote_services
from apps.reviews import services as reviews
from apps.reviews.models import (
    ClientDecision,
    Deliverable,
    DeliverableVersion,
    DeliverableVersionStatus,
    ReviewDecision,
)
from apps.tasks import services as task_services
from apps.tasks.models import (
    Assignment,
    AssignmentStatus,
    Task,
    TaskStatus,
)
from apps.tasks.transitions import Action, transition

from .conftest import pdf_upload


def _refresh(task: Task) -> Task:
    task.refresh_from_db()
    return task


def _advance_to_delivered(
    task: Task,
    *,
    owner,
    ops_user,
    quote_approver,
    specialist,
    reviewer,
    finance,
    amount_minor: int = 250_000,
):
    task = _refresh(
        transition(task=task, action=Action.TRIAGE, actor=ops_user)
    )
    task = _refresh(
        transition(task=task, action=Action.SCOPE, actor=quote_approver)
    )

    version = quote_services.create_version(
        task,
        quote_approver,
        scope_summary="Refresh the logo, supply print and digital exports.",
        exclusions="Photography is out of scope.",
        amount_minor=amount_minor,
        turnaround_days=5,
        revision_allowance=1,
    )
    quote_services.send_version(task, version, quote_approver)
    task = _refresh(task)

    outcome = quote_services.accept_quote(
        task=task,
        version=version,
        actor=owner,
        idempotency_key=f"lifecycle-{task.pk}",
    )
    task = _refresh(task)
    assert outcome.invoice is not None
    assert task.status == TaskStatus.AWAITING_PAYMENT

    billing.verify_payment(
        outcome.invoice,
        finance,
        reference=f"TRX-{task.reference}",
        amount_minor=amount_minor,
        currency="NGN",
    )
    task = _refresh(task)
    assert task.status == TaskStatus.READY_FOR_ASSIGNMENT

    task = _refresh(task_services.assign_task(task, ops_user, specialist))
    task = _refresh(task_services.accept_assignment(task, specialist))
    assert task.status == TaskStatus.IN_PROGRESS

    deliverable_version = task_services.submit_deliverable(
        task, specialist, pdf_upload("final-logo.pdf"), notes="Print-ready exports."
    )
    task = _refresh(task)
    assert task.status == TaskStatus.INTERNAL_REVIEW

    reviews.decide(
        task,
        deliverable_version,
        reviewer,
        decision=ReviewDecision.APPROVE,
        findings="Checked the exports and spacing.",
    )
    task = _refresh(task)
    assert task.status == TaskStatus.APPROVED_FOR_RELEASE

    reviews.release(task, deliverable_version, ops_user)
    task = _refresh(task)
    deliverable_version.refresh_from_db()
    assert task.status == TaskStatus.DELIVERED
    assert deliverable_version.status == DeliverableVersionStatus.RELEASED
    return task, deliverable_version


def test_full_request_lifecycle_through_client_acceptance(
    make_task,
    organization,
    owner,
    ops_user,
    quote_approver,
    specialist,
    reviewer,
    finance,
):
    task = make_task(organization, owner)
    assert task.status == TaskStatus.SUBMITTED

    task, version = _advance_to_delivered(
        task,
        owner=owner,
        ops_user=ops_user,
        quote_approver=quote_approver,
        specialist=specialist,
        reviewer=reviewer,
        finance=finance,
    )
    assert task.revision_allowance_remaining == 1

    reviews.client_decide(task, version, owner, decision=ClientDecision.ACCEPT)

    task = _refresh(task)
    assert task.status == TaskStatus.COMPLETED
    assert task.completed_at is not None
    assignment = Assignment.objects.get(task=task)
    assert assignment.status == AssignmentStatus.COMPLETED
    assert assignment.completed_at is not None

    event_actions = list(task.status_events.values_list("action", flat=True))
    assert "submit" in event_actions
    assert "quote_accepted_payment_required" in event_actions
    assert "payment_verified" in event_actions
    assert "approve_deliverable" in event_actions
    assert "release" in event_actions
    assert "client_accept" in event_actions


def test_revision_allowance_is_consumed_once(
    make_task,
    organization,
    owner,
    ops_user,
    quote_approver,
    specialist,
    reviewer,
    finance,
):
    task = make_task(organization, owner)
    task, first_version = _advance_to_delivered(
        task,
        owner=owner,
        ops_user=ops_user,
        quote_approver=quote_approver,
        specialist=specialist,
        reviewer=reviewer,
        finance=finance,
    )

    reviews.client_decide(
        task,
        first_version,
        owner,
        decision=ClientDecision.REVISION,
        note="Please widen the spacing in the wordmark.",
    )
    task = _refresh(task)
    assert task.status == TaskStatus.REVISION_REQUESTED
    assert task.revision_allowance_remaining == 0

    task = _refresh(task_services.start_revision(task, specialist))
    second_version = task_services.submit_deliverable(
        task, specialist, pdf_upload("final-logo-v2.pdf")
    )
    task = _refresh(task)
    assert second_version.version_number == 2
    assert task.status == TaskStatus.INTERNAL_REVIEW

    reviews.decide(task, second_version, reviewer, decision=ReviewDecision.APPROVE)
    task = _refresh(task)
    reviews.release(task, second_version, ops_user)
    task = _refresh(task)
    assert task.status == TaskStatus.DELIVERED

    with pytest.raises(ValidationFailure):
        reviews.client_decide(
            task,
            second_version,
            owner,
            decision=ClientDecision.REVISION,
            note="One more adjustment please.",
        )

    task = _refresh(task)
    assert task.status == TaskStatus.DELIVERED
    assert task.revision_allowance_remaining == 0


def test_reviewer_cannot_review_their_own_submission(
    make_task, organization, owner, specialist, reviewer
):
    task = make_task(organization, owner)
    Task.objects.filter(pk=task.pk).update(status=TaskStatus.INTERNAL_REVIEW)
    task.refresh_from_db()
    version = _manual_version(task, organization, submitted_by=reviewer)

    with pytest.raises(PermissionFailure):
        reviews.decide(task, version, reviewer, decision=ReviewDecision.APPROVE)


def test_assigned_specialist_cannot_review(
    make_task, organization, owner, reviewer, ops_user
):
    task = make_task(organization, owner)
    Task.objects.filter(pk=task.pk).update(status=TaskStatus.INTERNAL_REVIEW)
    task.refresh_from_db()
    Assignment.objects.create(
        task=task,
        specialist=reviewer,
        assigned_by=ops_user,
        status=AssignmentStatus.ACTIVE,
    )
    version = _manual_version(task, organization, submitted_by=owner)

    with pytest.raises(PermissionFailure):
        reviews.decide(task, version, reviewer, decision=ReviewDecision.APPROVE)


def test_rework_requires_findings_and_returns_to_work(
    make_task, organization, owner, ops_user, specialist, reviewer
):
    task = make_task(organization, owner)
    Task.objects.filter(pk=task.pk).update(status=TaskStatus.IN_PROGRESS)
    task.refresh_from_db()
    Assignment.objects.create(
        task=task,
        specialist=specialist,
        assigned_by=ops_user,
        status=AssignmentStatus.ACTIVE,
    )
    version = task_services.submit_deliverable(
        task, specialist, pdf_upload("draft.pdf")
    )
    task = _refresh(task)

    with pytest.raises(ValidationFailure):
        reviews.decide(task, version, reviewer, decision=ReviewDecision.REWORK)

    reviews.decide(
        task,
        version,
        reviewer,
        decision=ReviewDecision.REWORK,
        findings="The logo is low resolution at print size.",
    )
    task = _refresh(task)
    assert task.status == TaskStatus.REWORK_REQUIRED

    task = _refresh(task_services.resume_work(task, specialist))
    second = task_services.submit_deliverable(
        task, specialist, pdf_upload("draft-v2.pdf")
    )
    task = _refresh(task)
    assert second.version_number == 2
    assert task.status == TaskStatus.INTERNAL_REVIEW


def _manual_version(task: Task, organization, *, submitted_by) -> DeliverableVersion:
    private = PrivateFile.objects.create(
        organization=organization,
        purpose=FilePurpose.DELIVERABLE,
        storage_key=f"{organization.pk}/{uuid.uuid4().hex}.pdf",
        original_name="draft.pdf",
        size_bytes=10,
        content_type="application/pdf",
        sha256="0" * 64,
        scan_status=ScanStatus.CLEAN,
    )
    deliverable, _ = Deliverable.objects.get_or_create(task=task)
    return DeliverableVersion.objects.create(
        deliverable=deliverable,
        version_number=1,
        file=private,
        submitted_by=submitted_by,
    )
