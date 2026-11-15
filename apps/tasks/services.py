from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from apps.accounts.models import StaffRole
from apps.audit import services as audit_service
from apps.catalogue.models import ServiceState, ServiceVersion
from apps.core.exceptions import (
    InvalidTransition,
    PermissionFailure,
    ValidationFailure,
)
from apps.core.sequences import next_value

from . import transitions
from .models import (
    Assignment,
    AssignmentStatus,
    Priority,
    Task,
    TaskAttachment,
    TaskStatus,
    WorkLog,
)
from .transitions import Action


def _task_reference() -> str:
    return f"BAE-{next_value('task.reference', start=1000)}"


def _validated_text(value: str, *, field: str, minimum: int, maximum: int) -> str:
    text = (value or "").strip()
    if len(text) < minimum:
        raise ValidationFailure(f"{field} is too short.")
    if len(text) > maximum:
        raise ValidationFailure(f"{field} is too long.")
    return text


@transaction.atomic
def create_request(
    *,
    organization,
    user,
    service_version: ServiceVersion,
    title: str,
    brief: str,
    desired_outcome: str = "",
    priority: str = Priority.MEDIUM,
    due_date=None,
    files: list | None = None,
    work_country: str = "",
) -> Task:
    from apps.files.models import FilePurpose
    from apps.files.services import store_upload

    version = (
        ServiceVersion.objects.filter(pk=service_version.pk, is_current=True)
        .select_related("definition")
        .first()
    )
    if version is None or not version.definition.is_active:
        raise ValidationFailure("That service is no longer available.")
    if version.state not in (ServiceState.AVAILABLE, ServiceState.SCOPING_ONLY):
        raise ValidationFailure("That service is not accepting new requests.")
    if priority not in Priority.values:
        raise ValidationFailure("Choose a valid priority.")
    # The plan decides which service categories can be requested (Growth and
    # Dedicated categories stay locked on smaller plans).
    from apps.billing.plan_rules import require_category, require_not_paused

    require_not_paused(organization)
    require_category(organization, version.definition.category)

    # Where the work is for: fixed by the service if it has a country, else the client's
    # answer, else the business's own country. Staff confirm it during scoping.
    from apps.core.countries import BY_CODE

    work_country = (version.definition.work_country or work_country or organization.country or "").upper()
    if work_country and work_country not in BY_CODE:
        raise ValidationFailure("Choose the country this work is for.")

    task = Task.objects.create(
        reference=_task_reference(),
        organization=organization,
        created_by=user,
        service_version=version,
        title=_validated_text(title, field="Title", minimum=4, maximum=160),
        brief=_validated_text(brief, field="Brief", minimum=20, maximum=5000),
        desired_outcome=(desired_outcome or "").strip()[:3000],
        priority=priority,
        due_date=due_date,
        status=TaskStatus.DRAFT,
        revision_allowance_remaining=version.revision_allowance,
        work_country=work_country,
    )

    for uploaded in files or []:
        private_file = store_upload(
            organization=organization,
            user=user,
            uploaded_file=uploaded,
            purpose=FilePurpose.ATTACHMENT,
        )
        TaskAttachment.objects.create(task=task, file=private_file, added_by=user)

    task = transitions.transition(task=task, action=Action.SUBMIT, actor=user)
    return task


def request_information(task: Task, actor, question: str) -> Task:
    question = _validated_text(question, field="Question", minimum=5, maximum=2000)
    return transitions.transition(
        task=task,
        action=Action.REQUEST_INFORMATION,
        actor=actor,
        reason="Information requested from client",
        fields={"needs_info_question": question},
    )


def provide_information(task: Task, actor, information: str) -> Task:
    information = _validated_text(
        information, field="Information", minimum=3, maximum=4000
    )
    combined = "\n\n".join(
        part for part in (task.client_information, information) if part
    )
    return transitions.transition(
        task=task,
        action=Action.PROVIDE_INFORMATION,
        actor=actor,
        reason="Client supplied requested information",
        fields={"client_information": combined, "needs_info_question": ""},
    )


def begin_scoping(task: Task, actor) -> Task:
    return transitions.transition(task=task, action=Action.SCOPE, actor=actor)


@transaction.atomic
def assign_task(task: Task, actor, specialist, note: str = "") -> Task:
    if specialist.staff_role != StaffRole.SPECIALIST or not specialist.is_active:
        raise ValidationFailure("Choose an active specialist account.")
    if not specialist.email_verified:
        raise ValidationFailure("That specialist has not verified their email address.")
    if not specialist.mfa_enabled:
        raise PermissionFailure(
            "Specialists must enable two-factor authentication before taking work."
        )
    if task.assignments.filter(status=AssignmentStatus.ACTIVE).exists():
        raise ValidationFailure("This request already has an active assignment.")

    task = transitions.transition(
        task=task, action=Action.ASSIGN, actor=actor, reason=(note or "")[:500]
    )
    Assignment.objects.create(
        task=task,
        specialist=specialist,
        assigned_by=actor,
        status=AssignmentStatus.ACTIVE,
    )
    audit_service.record(
        action="task.assignment_created",
        actor=actor,
        organization_id=task.organization_id,
        resource_type="assignment",
        resource_id=str(task.pk),
        metadata={"specialist": str(specialist.pk)},
    )
    return task


@transaction.atomic
def accept_assignment(task: Task, actor) -> Task:
    task = transitions.transition(task=task, action=Action.ACCEPT_ASSIGNMENT, actor=actor)
    Assignment.objects.filter(
        task=task, specialist=actor, status=AssignmentStatus.ACTIVE
    ).update(accepted_at=task.updated_at)
    return task


def add_work_log(task: Task, actor, note: str, minutes: int | None = None) -> WorkLog:
    assignment = Assignment.objects.filter(
        task=task, specialist=actor, status=AssignmentStatus.ACTIVE
    ).first()
    if assignment is None:
        raise PermissionFailure("Only the assigned specialist can log work.")
    note = _validated_text(note, field="Note", minimum=3, maximum=4000)
    return WorkLog.objects.create(
        assignment=assignment, author=actor, note=note, minutes=minutes
    )


@transaction.atomic
def resume_work(task: Task, actor) -> Task:
    return transitions.transition(task=task, action=Action.RESUME_WORK, actor=actor)


@transaction.atomic
def start_revision(task: Task, actor) -> Task:
    return transitions.transition(task=task, action=Action.START_REVISION, actor=actor)


def hold_task(task: Task, actor, reason: str) -> Task:
    return transitions.transition(
        task=task, action=Action.HOLD, actor=actor, reason=reason
    )


def resume_task(task: Task, actor, reason: str = "") -> Task:
    return transitions.transition(
        task=task, action=Action.RESUME, actor=actor, reason=reason
    )


def cancel_by_client(task: Task, actor, reason: str) -> Task:
    from apps.billing.services import release_allowance

    task = transitions.transition(
        task=task, action=Action.CANCEL_CLIENT, actor=actor, reason=reason
    )
    release_allowance(task)
    audit_service.record(
        action="task.cancelled_by_client",
        actor=actor,
        organization_id=task.organization_id,
        resource_type="task",
        resource_id=str(task.pk),
        reason=reason,
    )
    return task


def cancel_by_staff(task: Task, actor, reason: str) -> Task:
    from apps.billing.services import release_allowance

    task = transitions.transition(
        task=task, action=Action.CANCEL_STAFF, actor=actor, reason=reason
    )
    release_allowance(task)
    audit_service.record(
        action="task.cancelled_by_staff",
        actor=actor,
        organization_id=task.organization_id,
        resource_type="task",
        resource_id=str(task.pk),
        reason=reason,
    )
    return task


@transaction.atomic
def submit_deliverable(task: Task, actor, uploaded_file, notes: str = ""):
    """Store a draft deliverable and move the task into independent review."""
    from apps.files.models import FilePurpose
    from apps.files.services import store_upload
    from apps.reviews.models import Deliverable, DeliverableVersion

    if not task.assignments.filter(
        status=AssignmentStatus.ACTIVE, specialist=actor
    ).exists():
        raise PermissionFailure("Only the assigned specialist can submit work.")
    if task.status not in (TaskStatus.IN_PROGRESS, TaskStatus.REWORK_REQUIRED):
        raise InvalidTransition("This request is not ready for a deliverable.")

    private_file = store_upload(
        organization=task.organization,
        user=actor,
        uploaded_file=uploaded_file,
        purpose=FilePurpose.DELIVERABLE,
    )
    deliverable, _ = Deliverable.objects.get_or_create(task=task)
    next_number = (
        deliverable.versions.aggregate(highest=Max("version_number"))["highest"] or 0
    ) + 1
    version = DeliverableVersion.objects.create(
        deliverable=deliverable,
        version_number=next_number,
        file=private_file,
        notes=(notes or "").strip()[:3000],
        submitted_by=actor,
    )
    transitions.transition(
        task=task,
        action=Action.SUBMIT_FOR_REVIEW,
        actor=actor,
        reason=f"Deliverable v{next_number} submitted",
    )
    audit_service.record(
        action="deliverable.submitted",
        actor=actor,
        organization_id=task.organization_id,
        resource_type="deliverable_version",
        resource_id=str(version.pk),
        metadata={"version": next_number},
    )
    return version


def confirm_work_country(task: Task, actor, country: str, reason: str = "") -> Task:
    """Staff confirm (or correct) the country the work is for, from the documents.

    Required before a quote is sent. A change needs a reason and is audited, so a
    client who picked their own country for foreign work is caught here.
    """
    from apps.accounts.permissions import TASK_SCOPE, require_perm
    from apps.audit import services as audit_service
    from apps.core.countries import BY_CODE

    require_perm(actor, TASK_SCOPE)
    country = (country or "").upper()
    if country not in BY_CODE:
        raise ValidationFailure("Choose the country this work is for.")
    with transaction.atomic():
        task = Task.objects.select_for_update().select_related("service_version__definition").get(pk=task.pk)
        if task.status != TaskStatus.SCOPING:
            raise ValidationFailure("The work country is confirmed while the request is being scoped.")
        fixed = task.service_version.definition.work_country
        if fixed and country != fixed:
            raise ValidationFailure("This service is always for one country.")
        previous = task.work_country
        if previous and country != previous and not (reason or "").strip():
            raise ValidationFailure("Say why the country is changing.")
        task.work_country = country
        task.work_country_confirmed_at = timezone.now()
        task.work_country_confirmed_by = actor
        task.save(update_fields=["work_country", "work_country_confirmed_at", "work_country_confirmed_by", "updated_at"])
        audit_service.record(
            action="task.work_country_changed" if previous and country != previous else "task.work_country_confirmed",
            actor=actor,
            organization_id=task.organization_id,
            resource_type="task",
            resource_id=str(task.pk),
            reason=(reason or "").strip()[:500],
            metadata={"from": previous, "to": country},
        )
    return task
