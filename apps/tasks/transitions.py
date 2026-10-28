from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import StaffRole
from apps.accounts.permissions import (
    FINANCE_VERIFY_PAYMENT,
    QUOTE_SEND,
    REVIEW_DECIDE,
    TASK_ASSIGN,
    TASK_CANCEL,
    TASK_HOLD,
    TASK_RELEASE,
    TASK_SCOPE,
    TASK_TRIAGE,
    has_perm,
)
from apps.audit import services as audit_service
from apps.audit.models import AuditEvent
from apps.core.context import get_correlation_id
from apps.core.exceptions import (
    InvalidTransition,
    PermissionFailure,
    StaleState,
    ValidationFailure,
)
from apps.organizations.access import membership_for

from .models import (
    AssignmentStatus,
    HOLDABLE_STATUSES,
    OPEN_STATUSES,
    Task,
    TaskStatus,
    TaskStatusEvent,
)


class Action(StrEnum):
    SUBMIT = "submit"
    TRIAGE = "triage"
    REQUEST_INFORMATION = "request_information"
    PROVIDE_INFORMATION = "provide_information"
    SCOPE = "scope"
    SEND_QUOTE = "send_quote"
    QUOTE_ACCEPTED_COVERED = "quote_accepted_covered"
    QUOTE_ACCEPTED_PAYMENT_REQUIRED = "quote_accepted_payment_required"
    QUOTE_REJECTED = "quote_rejected"
    PAYMENT_VERIFIED = "payment_verified"
    ASSIGN = "assign"
    ACCEPT_ASSIGNMENT = "accept_assignment"
    SUBMIT_FOR_REVIEW = "submit_for_review"
    APPROVE = "approve_deliverable"
    REQUEST_REWORK = "request_rework"
    RESUME_WORK = "resume_work"
    RELEASE = "release"
    CLIENT_ACCEPT = "client_accept"
    CLIENT_REQUEST_REVISION = "client_request_revision"
    START_REVISION = "start_revision"
    HOLD = "hold"
    RESUME = "resume"
    CANCEL_CLIENT = "cancel_client"
    CANCEL_STAFF = "cancel_staff"


@dataclass(frozen=True)
class Rule:
    to: str | None
    allow: tuple[str, ...]
    requires_reason: bool = False
    restore_status: bool = False


CLIENT_OWNER = "client.owner"
SPECIALIST_SELF = "specialist.self"

RULES: dict[tuple[str, Action], Rule] = {
    (TaskStatus.DRAFT, Action.SUBMIT): Rule(TaskStatus.SUBMITTED, (CLIENT_OWNER,)),
    (TaskStatus.SUBMITTED, Action.TRIAGE): Rule(TaskStatus.TRIAGE, (TASK_TRIAGE,)),
    (TaskStatus.TRIAGE, Action.REQUEST_INFORMATION): Rule(
        TaskStatus.NEEDS_INFORMATION, (TASK_TRIAGE, TASK_SCOPE)
    ),
    (TaskStatus.SCOPING, Action.REQUEST_INFORMATION): Rule(
        TaskStatus.NEEDS_INFORMATION, (TASK_SCOPE, TASK_TRIAGE)
    ),
    (TaskStatus.NEEDS_INFORMATION, Action.PROVIDE_INFORMATION): Rule(
        TaskStatus.TRIAGE, (CLIENT_OWNER,)
    ),
    (TaskStatus.TRIAGE, Action.SCOPE): Rule(TaskStatus.SCOPING, (TASK_SCOPE,)),
    (TaskStatus.SCOPING, Action.SEND_QUOTE): Rule(
        TaskStatus.AWAITING_CLIENT_APPROVAL, (QUOTE_SEND,)
    ),
    (TaskStatus.AWAITING_CLIENT_APPROVAL, Action.QUOTE_ACCEPTED_COVERED): Rule(
        TaskStatus.READY_FOR_ASSIGNMENT, (CLIENT_OWNER,)
    ),
    (TaskStatus.AWAITING_CLIENT_APPROVAL, Action.QUOTE_ACCEPTED_PAYMENT_REQUIRED): Rule(
        TaskStatus.AWAITING_PAYMENT, (CLIENT_OWNER,)
    ),
    (TaskStatus.AWAITING_CLIENT_APPROVAL, Action.QUOTE_REJECTED): Rule(
        TaskStatus.SCOPING, (CLIENT_OWNER,), requires_reason=True
    ),
    (TaskStatus.AWAITING_PAYMENT, Action.PAYMENT_VERIFIED): Rule(
        TaskStatus.READY_FOR_ASSIGNMENT, (FINANCE_VERIFY_PAYMENT,)
    ),
    (TaskStatus.READY_FOR_ASSIGNMENT, Action.ASSIGN): Rule(
        TaskStatus.ASSIGNED, (TASK_ASSIGN,)
    ),
    (TaskStatus.ASSIGNED, Action.ACCEPT_ASSIGNMENT): Rule(
        TaskStatus.IN_PROGRESS, (SPECIALIST_SELF,)
    ),
    (TaskStatus.IN_PROGRESS, Action.SUBMIT_FOR_REVIEW): Rule(
        TaskStatus.INTERNAL_REVIEW, (SPECIALIST_SELF,)
    ),
    (TaskStatus.INTERNAL_REVIEW, Action.APPROVE): Rule(
        TaskStatus.APPROVED_FOR_RELEASE, (REVIEW_DECIDE,)
    ),
    (TaskStatus.INTERNAL_REVIEW, Action.REQUEST_REWORK): Rule(
        TaskStatus.REWORK_REQUIRED, (REVIEW_DECIDE,), requires_reason=True
    ),
    (TaskStatus.REWORK_REQUIRED, Action.RESUME_WORK): Rule(
        TaskStatus.IN_PROGRESS, (SPECIALIST_SELF,)
    ),
    (TaskStatus.APPROVED_FOR_RELEASE, Action.RELEASE): Rule(
        TaskStatus.DELIVERED, (TASK_RELEASE,)
    ),
    (TaskStatus.DELIVERED, Action.CLIENT_ACCEPT): Rule(
        TaskStatus.COMPLETED, (CLIENT_OWNER,)
    ),
    (TaskStatus.DELIVERED, Action.CLIENT_REQUEST_REVISION): Rule(
        TaskStatus.REVISION_REQUESTED, (CLIENT_OWNER,), requires_reason=True
    ),
    (TaskStatus.REVISION_REQUESTED, Action.START_REVISION): Rule(
        TaskStatus.IN_PROGRESS, (SPECIALIST_SELF,)
    ),
    (TaskStatus.ON_HOLD, Action.RESUME): Rule(
        None, (TASK_HOLD,), restore_status=True
    ),
}

# Holds and cancellations apply to a range of states; expanding the table keeps
# every legal (status, action) pair explicit and testable.
for _status in HOLDABLE_STATUSES:
    RULES.setdefault((_status, Action.HOLD), Rule(TaskStatus.ON_HOLD, (TASK_HOLD,), requires_reason=True))

CANCELLABLE_BY_CLIENT = (
    TaskStatus.DRAFT,
    TaskStatus.SUBMITTED,
    TaskStatus.TRIAGE,
    TaskStatus.NEEDS_INFORMATION,
    TaskStatus.SCOPING,
    TaskStatus.AWAITING_CLIENT_APPROVAL,
    TaskStatus.AWAITING_PAYMENT,
    TaskStatus.READY_FOR_ASSIGNMENT,
)
for _status in CANCELLABLE_BY_CLIENT:
    RULES.setdefault(
        (_status, Action.CANCEL_CLIENT), Rule(TaskStatus.CANCELLED, (CLIENT_OWNER,), requires_reason=True)
    )
for _status in OPEN_STATUSES + (TaskStatus.ON_HOLD,):
    RULES.setdefault((_status, Action.CANCEL_STAFF), Rule(TaskStatus.CANCELLED, (TASK_CANCEL,), requires_reason=True))


def _actor_role_label(actor, task) -> str:
    staff_role = getattr(actor, "staff_role", "")
    if staff_role:
        return staff_role
    membership = membership_for(actor, task.organization_id)
    return membership.role if membership else ""


def authorize(task: Task, rule: Rule, actor) -> None:
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionFailure("Sign in to continue.")
    for token in rule.allow:
        if token == CLIENT_OWNER:
            membership = membership_for(actor, task.organization_id)
            if membership is not None and membership.is_owner:
                return
        elif token == SPECIALIST_SELF:
            if task.assignments.filter(
                status=AssignmentStatus.ACTIVE, specialist=actor
            ).exists():
                return
        elif has_perm(actor, token):
            return
    raise PermissionFailure("You do not have permission to perform this action.")


def _resolve_target(task: Task, rule: Rule) -> str:
    if rule.restore_status:
        if task.held_from_status not in OPEN_STATUSES:
            raise ValidationFailure("This request cannot be resumed automatically.")
        return task.held_from_status
    if rule.to is None:  # pragma: no cover - defensive
        raise ValidationFailure("This action needs a destination state.")
    return rule.to


def transition(
    *,
    task: Task,
    action: Action,
    actor,
    reason: str = "",
    fields: dict | None = None,
    extra_filter: dict | None = None,
    system: bool = False,
) -> Task:
    """Apply one legal state change with audit and concurrency protection.

    `extra_filter` adds preconditions to the conditional UPDATE so a competing
    request cannot slip past a guard (for example a revision allowance).

    `system` is reserved for trusted server-side callers that act on a verified
    provider event (for example a signed payment webhook). It skips the actor
    permission check but still enforces the transition table, reason rules,
    state preconditions and audit trail under a system actor.
    """
    rule = RULES.get((task.status, action))
    if rule is None:
        raise InvalidTransition(
            f"This request cannot be moved from {TaskStatus(task.status).label.lower()} "
            f"using that action."
        )

    if not system:
        try:
            authorize(task, rule, actor)
        except PermissionFailure:
            audit_service.record(
                action=f"task.{action.value}",
                actor=actor,
                organization_id=task.organization_id,
                resource_type="task",
                resource_id=str(task.pk),
                result=AuditEvent.Result.DENIED,
                reason=reason,
                metadata={"from": task.status},
            )
            raise

    if rule.requires_reason and not (reason or "").strip():
        raise ValidationFailure("Give a short reason for this action.")

    updates: dict = dict(fields or {})
    forbidden = {"status", "id", "reference", "organization", "created_by", "created_at"}
    if forbidden & set(updates):
        raise ValidationFailure("That change is not allowed here.")

    with transaction.atomic():
        locked = Task.objects.select_for_update().get(pk=task.pk)
        if locked.status != task.status:
            raise StaleState("This request changed while you were working. Refresh and retry.")
        target = _resolve_target(locked, rule)
        updates.update(status=target, updated_at=timezone.now())
        if action == Action.HOLD:
            updates["held_from_status"] = locked.status
        if action == Action.RESUME:
            updates["held_from_status"] = ""
        if target == TaskStatus.COMPLETED:
            updates["completed_at"] = timezone.now()

        filter_kwargs = {"pk": locked.pk, "status": locked.status, **(extra_filter or {})}
        updated = Task.objects.filter(**filter_kwargs).update(**updates)
        if not updated:
            raise StaleState("This request changed while you were working. Refresh and retry.")

        TaskStatusEvent.objects.create(
            task=locked,
            from_status=locked.status,
            to_status=target,
            action=action.value,
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            actor_role=_actor_role_label(actor, locked)[:40],
            reason=(reason or "")[:2000],
            correlation_id=get_correlation_id(),
        )
        audit_service.record(
            action=f"task.{action.value}",
            actor=actor,
            organization_id=locked.organization_id,
            resource_type="task",
            resource_id=str(locked.pk),
            reason=reason,
            metadata={"from": locked.status, "to": target},
        )

    return Task.objects.get(pk=task.pk)


def available_actions(task: Task, actor) -> set[str]:
    """Actions the actor may take, for rendering. Never the authorization check."""
    allowed: set[str] = set()
    for (status, action), rule in RULES.items():
        if status != task.status:
            continue
        try:
            authorize(task, rule, actor)
            resolve_ok = True
            if rule.restore_status:
                resolve_ok = bool(task.held_from_status)
            if resolve_ok:
                allowed.add(action.value)
        except (PermissionFailure, ValidationFailure):
            continue
    return allowed
