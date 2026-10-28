from __future__ import annotations

import pytest

from apps.audit.models import AuditEvent
from apps.core.exceptions import (
    InvalidTransition,
    StaleState,
    ValidationFailure,
    PermissionFailure,
)
from apps.tasks import services
from apps.tasks.models import Task, TaskStatus
from apps.tasks.transitions import Action, available_actions, transition


def _set_status(task: Task, status: str) -> Task:
    Task.objects.filter(pk=task.pk).update(status=status)
    task.refresh_from_db()
    return task


def test_legal_transition_records_status_and_audit(submitted_task, ops_user):
    task = transition(task=submitted_task, action=Action.TRIAGE, actor=ops_user)

    assert task.status == TaskStatus.TRIAGE
    event = task.status_events.first()
    assert (event.from_status, event.to_status) == (
        TaskStatus.SUBMITTED,
        TaskStatus.TRIAGE,
    )
    assert event.actor == ops_user
    assert event.actor_role == "operations"
    assert AuditEvent.objects.filter(
        action="task.triage", resource_id=str(task.pk)
    ).exists()


def test_illegal_action_is_rejected_without_side_effects(submitted_task):
    before = submitted_task.status_events.count()

    with pytest.raises(InvalidTransition):
        transition(
            task=submitted_task,
            action=Action.RELEASE,
            actor=submitted_task.created_by,
        )

    assert submitted_task.status_events.count() == before


def test_client_cannot_perform_staff_transition(submitted_task, owner):
    with pytest.raises(PermissionFailure):
        transition(task=submitted_task, action=Action.TRIAGE, actor=owner)

    assert AuditEvent.objects.filter(
        action="task.triage",
        result=AuditEvent.Result.DENIED,
        resource_id=str(submitted_task.pk),
    ).exists()


def test_stale_object_cannot_replay_transition(submitted_task, ops_user):
    stale = Task.objects.get(pk=submitted_task.pk)
    transition(task=submitted_task, action=Action.TRIAGE, actor=ops_user)

    with pytest.raises(StaleState):
        transition(task=stale, action=Action.TRIAGE, actor=ops_user)

    assert Task.objects.get(pk=submitted_task.pk).status == TaskStatus.TRIAGE


def test_reason_is_required_for_cancel(submitted_task, owner):
    with pytest.raises(ValidationFailure):
        transition(
            task=submitted_task, action=Action.CANCEL_CLIENT, actor=owner, reason="  "
        )

    submitted_task.refresh_from_db()
    assert submitted_task.status == TaskStatus.SUBMITTED


def test_hold_and_resume_restore_previous_status(submitted_task, ops_user):
    task = transition(task=submitted_task, action=Action.TRIAGE, actor=ops_user)
    task = transition(
        task=task, action=Action.HOLD, actor=ops_user, reason="Waiting on the client"
    )
    assert task.status == TaskStatus.ON_HOLD
    assert task.held_from_status == TaskStatus.TRIAGE

    task = transition(task=task, action=Action.RESUME, actor=ops_user)
    assert task.status == TaskStatus.TRIAGE
    assert task.held_from_status == ""


def test_cancel_is_not_legal_once_work_is_in_progress(
    submitted_task, ops_user, owner
):
    task = _set_status(submitted_task, TaskStatus.IN_PROGRESS)

    with pytest.raises(InvalidTransition):
        transition(
            task=task,
            action=Action.CANCEL_CLIENT,
            actor=owner,
            reason="Changed my mind",
        )


def test_system_transition_keeps_table_and_audit_but_needs_no_actor(
    submitted_task, ops_user
):
    task = _set_status(submitted_task, TaskStatus.AWAITING_PAYMENT)

    task = transition(
        task=task,
        action=Action.PAYMENT_VERIFIED,
        actor=None,
        system=True,
        reason="Signed provider webhook",
    )

    assert task.status == TaskStatus.READY_FOR_ASSIGNMENT
    event = task.status_events.first()
    assert event.actor is None
    assert event.reason == "Signed provider webhook"


def test_system_flag_does_not_bypass_reason_rules(submitted_task):
    task = _set_status(submitted_task, TaskStatus.TRIAGE)

    with pytest.raises(ValidationFailure):
        transition(
            task=task,
            action=Action.CANCEL_STAFF,
            actor=None,
            system=True,
            reason="",
        )


def test_available_actions_matches_server_authorization(submitted_task, owner, specialist):
    owner_actions = available_actions(submitted_task, owner)
    assert Action.CANCEL_CLIENT.value in owner_actions

    specialist_actions = available_actions(submitted_task, specialist)
    assert specialist_actions == set()


def test_transition_fields_cannot_override_core_columns(submitted_task, ops_user):
    with pytest.raises(ValidationFailure):
        transition(
            task=submitted_task,
            action=Action.TRIAGE,
            actor=ops_user,
            fields={"organization": "attacker-chosen"},
        )


def test_cancelled_request_is_terminal(submitted_task, owner):
    task = services.cancel_by_client(submitted_task, owner, "No longer needed")

    with pytest.raises(InvalidTransition):
        transition(
            task=task,
            action=Action.CANCEL_STAFF,
            actor=owner,
            reason="Again",
        )
