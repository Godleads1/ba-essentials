from __future__ import annotations

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.permissions import REVIEW_DECIDE, require_perm
from apps.audit import services as audit_service
from apps.billing import services as billing
from apps.core.exceptions import (
    InvalidTransition,
    PaymentRequired,
    PermissionFailure,
    ValidationFailure,
)
from apps.tasks import transitions
from apps.tasks.models import AssignmentStatus, Task, TaskStatus
from apps.tasks.transitions import Action

from .models import (
    ClientAcceptance,
    ClientDecision,
    DeliverableVersion,
    DeliverableVersionStatus,
    Review,
    ReviewDecision,
)


def _require_independent_reviewer(task: Task, version: DeliverableVersion, actor) -> None:
    if version.submitted_by_id == actor.pk:
        raise PermissionFailure(
            "Reviewers cannot review their own work. Ask another reviewer."
        )
    if task.assignments.filter(
        status=AssignmentStatus.ACTIVE, specialist=actor
    ).exists():
        raise PermissionFailure("The assigned specialist cannot review this work.")


@transaction.atomic
def decide(
    task: Task,
    version: DeliverableVersion,
    actor,
    *,
    decision: str,
    findings: str = "",
) -> DeliverableVersion:
    require_perm(actor, REVIEW_DECIDE)
    version = DeliverableVersion.objects.select_for_update().get(pk=version.pk)
    task = Task.objects.select_for_update().get(pk=task.pk)
    if version.deliverable.task_id != task.pk:
        raise ValidationFailure("That deliverable belongs to another request.")
    if task.status != TaskStatus.INTERNAL_REVIEW or version.status != (
        DeliverableVersionStatus.PENDING_REVIEW
    ):
        raise InvalidTransition("That deliverable is not awaiting review.")
    _require_independent_reviewer(task, version, actor)
    if decision not in ReviewDecision.values:
        raise ValidationFailure("Choose a review decision.")
    if decision == ReviewDecision.REWORK and not (findings or "").strip():
        raise ValidationFailure("Explain what must change before work returns.")

    Review.objects.create(
        deliverable_version=version,
        reviewer=actor,
        decision=decision,
        findings=(findings or "").strip(),
    )
    version.reviewed_by = actor
    version.reviewed_at = timezone.now()
    version.review_note = (findings or "").strip()
    version.status = (
        DeliverableVersionStatus.APPROVED
        if decision == ReviewDecision.APPROVE
        else DeliverableVersionStatus.REWORK_REQUIRED
    )
    version.save(
        update_fields=["reviewed_by", "reviewed_at", "review_note", "status"]
    )

    transitions.transition(
        task=task,
        action=(
            Action.APPROVE if decision == ReviewDecision.APPROVE else Action.REQUEST_REWORK
        ),
        actor=actor,
        reason=(findings or "").strip(),
    )
    return version


@transaction.atomic
def release(task: Task, version: DeliverableVersion, actor) -> DeliverableVersion:
    version = DeliverableVersion.objects.select_for_update().get(pk=version.pk)
    task = Task.objects.select_for_update().get(pk=task.pk)
    if version.deliverable.task_id != task.pk:
        raise ValidationFailure("That deliverable belongs to another request.")
    if task.status != TaskStatus.APPROVED_FOR_RELEASE:
        raise InvalidTransition("This request is not approved for release.")
    if version.status != DeliverableVersionStatus.APPROVED:
        raise ValidationFailure("Only an approved deliverable can be released.")

    eligibility = billing.eligibility(task)
    if not eligibility.allowed:
        raise PaymentRequired(
            f"Release is blocked: {eligibility.reason} Ask finance to reconcile payment."
        )

    version.status = DeliverableVersionStatus.RELEASED
    version.released_at = timezone.now()
    version.save(update_fields=["status", "released_at"])
    transitions.transition(
        task=task,
        action=Action.RELEASE,
        actor=actor,
        reason=f"Deliverable v{version.version_number} released",
    )
    audit_service.record(
        action="deliverable.released",
        actor=actor,
        organization_id=task.organization_id,
        resource_type="deliverable_version",
        resource_id=str(version.pk),
        metadata={"version": version.version_number},
    )
    return version


@transaction.atomic
def client_decide(
    task: Task,
    version: DeliverableVersion,
    actor,
    *,
    decision: str,
    note: str = "",
) -> ClientAcceptance:
    task = Task.objects.select_for_update().get(pk=task.pk)
    version = DeliverableVersion.objects.select_for_update().get(pk=version.pk)
    if task.status != TaskStatus.DELIVERED:
        raise InvalidTransition("There is no released deliverable to respond to.")
    if version.deliverable.task_id != task.pk or version.status != (
        DeliverableVersionStatus.RELEASED
    ):
        raise ValidationFailure("That deliverable is not available for a decision.")
    if decision not in ClientDecision.values:
        raise ValidationFailure("Choose a response.")

    if decision == ClientDecision.REVISION:
        if task.revision_allowance_remaining < 1:
            raise ValidationFailure(
                "The included revision allowance is used up. Ask the team for a quote."
            )
        if not (note or "").strip():
            raise ValidationFailure("Explain what should change in the revision.")
        transitions.transition(
            task=task,
            action=Action.CLIENT_REQUEST_REVISION,
            actor=actor,
            reason=note,
            fields={"revision_allowance_remaining": F("revision_allowance_remaining") - 1},
            extra_filter={"revision_allowance_remaining__gt": 0},
        )
    else:
        transitions.transition(
            task=task,
            action=Action.CLIENT_ACCEPT,
            actor=actor,
            reason=(note or "")[:500],
        )
        task.assignments.filter(status=AssignmentStatus.ACTIVE).update(
            status=AssignmentStatus.COMPLETED, completed_at=timezone.now()
        )

    acceptance = ClientAcceptance.objects.create(
        task=task,
        deliverable_version=version,
        decision=decision,
        note=(note or "").strip(),
        decided_by=actor,
    )
    audit_service.record(
        action=f"deliverable.client_{decision}",
        actor=actor,
        organization_id=task.organization_id,
        resource_type="deliverable_version",
        resource_id=str(version.pk),
    )
    return acceptance
