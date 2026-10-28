from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import StaffRole
from apps.audit import services as audit_services
from apps.billing import services as billing
from apps.quotes import services as quote_services
from apps.tasks import services as task_services
from apps.tasks.transitions import Action, transition

from .conftest import login_client, make_user, pdf_upload

QUEUE_URLS = (
    "staff-needs-information",
    "staff-scoping",
    "staff-workload",
    "staff-deliverables",
    "staff-audit",
)


@pytest.fixture
def support_user(db):
    return make_user(
        "support@example.test", staff_role=StaffRole.SUPPORT, mfa=True, is_staff=True
    )


@pytest.fixture
def support_client(support_user):
    return login_client(support_user)


@pytest.fixture
def admin_client(db):
    admin = make_user(
        "admin@example.test", staff_role=StaffRole.ADMIN, mfa=True, is_staff=True
    )
    return login_client(admin)


def _refresh(task):
    task.refresh_from_db()
    return task


def _to_ready_for_assignment(
    task, *, owner, ops_user, quote_approver, finance, amount_minor=250_000
):
    task = _refresh(transition(task=task, action=Action.TRIAGE, actor=ops_user))
    task = _refresh(transition(task=task, action=Action.SCOPE, actor=quote_approver))
    version = quote_services.create_version(
        task,
        quote_approver,
        scope_summary="Synthetic scope for queue coverage.",
        exclusions="",
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
        idempotency_key=f"queue-{task.pk}",
    )
    task = _refresh(task)
    billing.verify_payment(
        outcome.invoice,
        finance,
        reference=f"TRX-{task.reference}",
        amount_minor=amount_minor,
        currency="NGN",
    )
    return _refresh(task)


def _to_internal_review(
    task, *, owner, ops_user, quote_approver, specialist, finance
):
    task = _to_ready_for_assignment(
        task,
        owner=owner,
        ops_user=ops_user,
        quote_approver=quote_approver,
        finance=finance,
    )
    task = _refresh(task_services.assign_task(task, ops_user, specialist))
    task = _refresh(task_services.accept_assignment(task, specialist))
    version = task_services.submit_deliverable(
        task, specialist, pdf_upload("queue-draft.pdf"), notes="Synthetic draft."
    )
    return _refresh(task), version


def test_needs_information_queue_lists_waiting_clients(
    submitted_task, ops_user, staff_client
):
    task = _refresh(transition(task=submitted_task, action=Action.TRIAGE, actor=ops_user))
    task_services.request_information(
        task, ops_user, "Share the original vector source file."
    )

    response = staff_client.get(reverse("staff-needs-information"))

    assert response.status_code == 200
    content = response.content.decode()
    assert task.reference in content
    assert "Share the original vector source file." in content


def test_scoping_queue_shows_the_latest_quote(
    scoping_task, quote_approver, staff_client
):
    version = quote_services.create_version(
        scoping_task,
        quote_approver,
        scope_summary="Synthetic scope for the queue.",
        exclusions="",
        amount_minor=250_000,
        turnaround_days=5,
        revision_allowance=1,
    )
    quote_services.send_version(scoping_task, version, quote_approver)

    response = staff_client.get(reverse("staff-scoping"))

    assert response.status_code == 200
    content = response.content.decode()
    assert scoping_task.reference in content
    assert "v1" in content
    assert "NGN 2,500.00" in content


def test_deliverables_queue_lists_drafts_in_review(
    make_task,
    organization,
    owner,
    ops_user,
    quote_approver,
    specialist,
    finance,
    staff_client,
):
    task = make_task(organization, owner)
    task, _version = _to_internal_review(
        task,
        owner=owner,
        ops_user=ops_user,
        quote_approver=quote_approver,
        specialist=specialist,
        finance=finance,
    )

    response = staff_client.get(reverse("staff-deliverables"))

    assert response.status_code == 200
    content = response.content.decode()
    assert task.reference in content
    assert "v1" in content


def test_workload_lists_specialist_cases_and_ready_requests(
    make_task,
    organization,
    owner,
    ops_user,
    quote_approver,
    specialist,
    finance,
    staff_client,
):
    active_task = make_task(organization, owner, title="Active synthetic case")
    active_task, _version = _to_internal_review(
        active_task,
        owner=owner,
        ops_user=ops_user,
        quote_approver=quote_approver,
        specialist=specialist,
        finance=finance,
    )
    ready_task = make_task(organization, owner, title="Unassigned synthetic case")
    _to_ready_for_assignment(
        ready_task,
        owner=owner,
        ops_user=ops_user,
        quote_approver=quote_approver,
        finance=finance,
    )

    response = staff_client.get(reverse("staff-workload"))

    assert response.status_code == 200
    content = response.content.decode()
    assert specialist.email in content
    assert active_task.reference in content
    assert ready_task.reference in content


def test_my_cases_is_limited_to_the_signed_in_specialist(
    make_task,
    organization,
    owner,
    ops_user,
    quote_approver,
    specialist,
    finance,
):
    other_specialist = make_user(
        "specialist-two@example.test",
        staff_role=StaffRole.SPECIALIST,
        mfa=True,
        is_staff=True,
    )
    task = make_task(organization, owner)
    task, _version = _to_internal_review(
        task,
        owner=owner,
        ops_user=ops_user,
        quote_approver=quote_approver,
        specialist=specialist,
        finance=finance,
    )

    mine = login_client(specialist).get(reverse("staff-my-cases"))
    theirs = login_client(other_specialist).get(reverse("staff-my-cases"))

    assert task.reference in mine.content.decode()
    assert task.reference not in theirs.content.decode()


def test_support_role_cannot_open_operations_queues(support_client):
    for name in QUEUE_URLS:
        response = support_client.get(reverse(name))
        assert response.status_code == 302, name
        assert response["Location"] == reverse("staff-dashboard"), name


def test_audit_log_renders_events_for_audit_viewers(
    admin_client, organization, ops_user, submitted_task
):
    audit_services.record(
        action="synthetic.queue_check",
        actor=ops_user,
        organization=organization,
        resource_type="task",
        resource_id=str(submitted_task.pk),
        reason="Synthetic audit coverage",
    )

    response = admin_client.get(reverse("staff-audit"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "synthetic.queue_check" in content
    assert "Synthetic audit coverage" in content
