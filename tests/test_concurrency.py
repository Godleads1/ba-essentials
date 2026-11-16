from __future__ import annotations

import threading

import pytest
from django.db import close_old_connections, connection

from apps.accounts.models import User
from apps.billing import services as billing
from apps.billing.models import (
    Invoice,
    InvoiceStatus,
    PaymentAttempt,
    PaymentStatus,
    Plan,
    UsageKind,
    UsageLedger,
)
from apps.core.exceptions import InvalidTransition, StaleState, ValidationFailure
from apps.quotes import services as quotes
from apps.quotes.models import QuoteAcceptance, QuoteVersion
from apps.tasks.models import Task, TaskStatus, TaskStatusEvent
from apps.tasks.transitions import Action, transition

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="row-lock races require PostgreSQL; SQLite serialises writers",
    ),
]

TIMEOUT = 15


def _race(*workers):
    """Run workers at the same barrier, each on its own database connection."""
    barrier = threading.Barrier(len(workers))
    results: list[tuple[str, object]] = []
    results_lock = threading.Lock()

    def runner(worker):
        close_old_connections()
        try:
            outcome = ("ok", worker(barrier))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertions
            outcome = ("error", exc)
        finally:
            close_old_connections()
        with results_lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=runner, args=(worker,), daemon=True)
        for worker in workers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT)

    assert not any(thread.is_alive() for thread in threads), "a worker did not finish"
    assert len(results) == len(workers)
    return results


def _values(results):
    return [value for kind, value in results if kind == "ok"]


def _errors(results):
    return [value for kind, value in results if kind == "error"]


def _sent_quote(scoping_task, quote_approver, *, amount_minor: int = 150_000):
    version = quotes.create_version(
        scoping_task,
        quote_approver,
        scope_summary="Refresh the logo and prepare print-ready exports.",
        exclusions="Copywriting and photography are not included.",
        amount_minor=amount_minor,
        turnaround_days=5,
        revision_allowance=1,
    )
    return quotes.send_version(scoping_task, version, quote_approver)


def test_concurrent_transitions_allow_exactly_one_winner(submitted_task, ops_user):
    task_id = submitted_task.pk
    actor_id = ops_user.pk

    def triage(barrier):
        task = Task.objects.get(pk=task_id)
        actor = User.objects.get(pk=actor_id)
        barrier.wait(timeout=TIMEOUT)
        return transition(task=task, action=Action.TRIAGE, actor=actor)

    results = _race(triage, triage)

    assert len(_values(results)) == 1
    errors = _errors(results)
    assert len(errors) == 1
    assert isinstance(errors[0], StaleState)

    task = Task.objects.get(pk=task_id)
    assert task.status == TaskStatus.TRIAGE
    assert (
        TaskStatusEvent.objects.filter(
            task=task, action=Action.TRIAGE.value
        ).count()
        == 1
    )


def test_concurrent_acceptance_with_different_keys_creates_one_invoice(
    scoping_task, quote_approver, owner
):
    version = _sent_quote(scoping_task, quote_approver)
    task_id, version_id, actor_id = scoping_task.pk, version.pk, owner.pk

    def accept(key):
        def worker(barrier):
            task = Task.objects.get(pk=task_id)
            quote_version = QuoteVersion.objects.get(pk=version_id)
            actor = User.objects.get(pk=actor_id)
            barrier.wait(timeout=TIMEOUT)
            return quotes.accept_quote(
                task=task,
                version=quote_version,
                actor=actor,
                idempotency_key=key,
            )

        return worker

    results = _race(accept("accept-one"), accept("accept-two"))

    assert len(_values(results)) == 1
    errors = _errors(results)
    assert len(errors) == 1
    assert isinstance(errors[0], (InvalidTransition, ValidationFailure))
    assert Invoice.objects.filter(task_id=task_id).count() == 1
    assert QuoteAcceptance.objects.filter(quote_version_id=version_id).count() == 1
    assert (
        TaskStatusEvent.objects.filter(
            task_id=task_id,
            action=Action.QUOTE_ACCEPTED_PAYMENT_REQUIRED.value,
        ).count()
        == 1
    )


def test_concurrent_acceptance_with_the_same_key_replays_once(
    scoping_task, quote_approver, owner
):
    version = _sent_quote(scoping_task, quote_approver)
    task_id, version_id, actor_id = scoping_task.pk, version.pk, owner.pk

    def accept(barrier):
        task = Task.objects.get(pk=task_id)
        quote_version = QuoteVersion.objects.get(pk=version_id)
        actor = User.objects.get(pk=actor_id)
        barrier.wait(timeout=TIMEOUT)
        return quotes.accept_quote(
            task=task,
            version=quote_version,
            actor=actor,
            idempotency_key="shared-key",
        )

    results = _race(accept, accept)

    assert _errors(results) == []
    outcomes = _values(results)
    assert len(outcomes) == 2
    assert sorted(outcome.replayed for outcome in outcomes) == [False, True]
    assert Invoice.objects.filter(task_id=task_id).count() == 1
    assert QuoteAcceptance.objects.filter(quote_version_id=version_id).count() == 1


def test_concurrent_allowance_reservation_respects_the_plan_limit(
    organization, owner, plan, subscription, make_task
):
    Plan.objects.filter(pk=plan.pk).update(monthly_task_allowance=1)
    first = make_task(organization, owner)
    second = make_task(organization, owner)

    def reserve(task_id):
        def worker(barrier):
            task = Task.objects.get(pk=task_id)
            barrier.wait(timeout=TIMEOUT)
            return billing.reserve_allowance(task)

        return worker

    results = _race(reserve(first.pk), reserve(second.pk))

    assert _errors(results) == []
    assert sorted(_values(results)) == [False, True]
    assert (
        UsageLedger.objects.filter(kind=UsageKind.ALLOWANCE).count() == 1
    )


def test_concurrent_payment_verification_records_one_payment(
    scoping_task, quote_approver, owner, finance
):
    version = _sent_quote(scoping_task, quote_approver)
    outcome = quotes.accept_quote(
        task=scoping_task,
        version=version,
        actor=owner,
        idempotency_key="invoice-for-payment-race",
    )
    assert outcome.invoice is not None
    invoice_id = outcome.invoice.pk
    amount_minor = outcome.invoice.amount_minor
    currency = outcome.invoice.currency
    task_id, actor_id = scoping_task.pk, finance.pk

    def verify(reference):
        def worker(barrier):
            invoice = Invoice.objects.get(pk=invoice_id)
            actor = User.objects.get(pk=actor_id)
            barrier.wait(timeout=TIMEOUT)
            return billing.verify_payment(
                invoice,
                actor,
                reference=reference,
                amount_minor=amount_minor,
                currency=currency,
            )

        return worker

    results = _race(verify("BANK-RACE-0001"), verify("BANK-RACE-0002"))

    assert len(_values(results)) == 1
    errors = _errors(results)
    assert len(errors) == 1
    assert isinstance(errors[0], ValidationFailure)
    assert (
        PaymentAttempt.objects.filter(
            invoice_id=invoice_id, status=PaymentStatus.SUCCEEDED
        ).count()
        == 1
    )

    invoice = Invoice.objects.get(pk=invoice_id)
    assert invoice.status == InvoiceStatus.PAID
    task = Task.objects.get(pk=task_id)
    assert task.status == TaskStatus.READY_FOR_ASSIGNMENT
