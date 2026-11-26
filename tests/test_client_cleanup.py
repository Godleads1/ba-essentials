"""remove_client_data: the pre-launch clean-up of test businesses and client accounts."""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.billing.models import Invoice, PaymentAttempt, PaymentStatus, Plan
from apps.catalogue.models import ServiceVersion
from apps.core import client_cleanup
from apps.core.models import OutboxEvent
from apps.files.models import PrivateFile
from apps.files.storage import get_storage
from apps.organizations.models import Organization
from apps.reviews import services as reviews
from apps.reviews.models import ClientDecision
from apps.tasks.models import Task

from .test_lifecycle import _advance_to_delivered

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def full_history(make_task, organization, owner, member, other_organization, other_owner, ops_user,
                 quote_approver, specialist, reviewer, finance, request):
    """A business with a paid quote, payment record, released deliverable and client acceptance."""
    task, version = _advance_to_delivered(make_task(organization, owner), owner=owner, ops_user=ops_user,
                                          quote_approver=quote_approver, specialist=specialist,
                                          reviewer=reviewer, finance=finance)
    reviews.client_decide(task, version, owner, decision=ClientDecision.ACCEPT)
    invoice = Invoice.objects.get(task=task)
    PaymentAttempt.objects.create(invoice=invoice, provider="paystack", reference="PS-CLEAN-1",
                                  amount_minor=invoice.amount_minor, currency="NGN",
                                  status=PaymentStatus.INITIATED)
    request.getfixturevalue("subscription")             # a plan subscription too, added after the paid quote
    make_task(other_organization, other_owner, title="Second business request")
    OutboxEvent.objects.create(topic=OutboxEvent.Topic.EMAIL,
                               payload={"to": [owner.email], "subject": "s", "body": "b"})
    OutboxEvent.objects.create(topic=OutboxEvent.Topic.EMAIL,
                               payload={"to": [ops_user.email], "subject": "staff", "body": "b"})
    return version


def _run(*args):
    out = StringIO()
    call_command("remove_client_data", *args, stdout=out)
    return out.getvalue()


def test_dry_run_changes_nothing(full_history):
    before = (Organization.objects.count(), User.objects.count(), Task.objects.count(), Invoice.objects.count())
    output = _run()
    assert "DRY RUN: nothing changed" in output and "2 businesses" in output
    assert "--confirm delete-all-client-data" in output
    assert (Organization.objects.count(), User.objects.count(), Task.objects.count(),
            Invoice.objects.count()) == before
    assert not AuditEvent.objects.filter(action="data.client_data_removed").exists()
    assert get_storage().exists(full_history.file.storage_key)


def test_confirmed_run_removes_clients_and_keeps_staff(full_history, owner, ops_user, specialist, reviewer,
                                                       finance, quote_approver, plan):
    key = full_history.file.storage_key
    staff = {u.pk for u in (ops_user, specialist, reviewer, finance, quote_approver)}
    audit_before = AuditEvent.objects.count()
    output = _run("--confirm", "delete-all-client-data")
    assert "REMOVED: 2 businesses" in output and "Done." in output

    assert not Organization.objects.exists()
    assert not Task.objects.exists() and not Invoice.objects.exists() and not PaymentAttempt.objects.exists()
    assert not PrivateFile.objects.exists()
    assert not get_storage().exists(key)                       # the stored file is gone too
    assert not client_cleanup.client_users().exists()
    assert set(User.objects.values_list("pk", flat=True)) == staff
    # Plans and the catalogue stay.
    assert Plan.objects.filter(pk=plan.pk).exists() and ServiceVersion.objects.exists()
    # The audit trail stays, unlinked from removed businesses, with one event recording the counts.
    assert AuditEvent.objects.count() > audit_before
    assert not AuditEvent.objects.filter(organization__isnull=False).exists()
    event = AuditEvent.objects.get(action="data.client_data_removed")
    assert event.metadata["businesses"] == 2 and event.metadata["people"] >= 3
    # Unsent email to a removed client is dropped; staff email stays.
    subjects = list(OutboxEvent.objects.filter(processed_at__isnull=True).values_list("payload__subject", flat=True))
    assert "staff" in subjects and "s" not in subjects


def test_superadmin_and_django_admin_are_kept(full_history):
    boss = User.objects.create_superuser(email="boss@example.test", password="x" * 20)
    _run("--confirm", "delete-all-client-data")
    assert User.objects.filter(pk=boss.pk).exists()


def test_refuses_with_live_payment_keys(full_history, settings):
    settings.PAYSTACK_SECRET_KEY = "sk_live_abc"
    with pytest.raises(CommandError, match="Live payment keys are set"):
        _run("--confirm", "delete-all-client-data")
    assert Organization.objects.count() == 2


def test_refuses_with_a_legal_hold(full_history, organization):
    Organization.objects.filter(pk=organization.pk).update(legal_hold=True)
    with pytest.raises(CommandError, match="legal hold"):
        _run("--confirm", "delete-all-client-data")
    assert Organization.objects.count() == 2


def test_a_wrong_confirmation_changes_nothing(full_history):
    with pytest.raises(CommandError, match="Nothing was changed"):
        _run("--confirm", "yes")
    assert Organization.objects.count() == 2


def test_running_twice_is_harmless(full_history):
    _run("--confirm", "delete-all-client-data")
    assert "REMOVED: 0 businesses, 0 client accounts" in _run("--confirm", "delete-all-client-data")
