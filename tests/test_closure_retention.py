"""Step 5c: closing an account at the paid date, read-only afterwards, and the retention job."""
from __future__ import annotations

import time
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import ConsentRecord, User, UserSession
from apps.audit.models import AuditEvent
from apps.billing import renewals
from apps.billing.models import Invoice, SubscriptionStatus
from apps.communications.models import SupportTicket, SupportTicketStatus, TaskMessage
from apps.core import retention
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.organizations import closure
from apps.organizations.models import Membership, Organization
from apps.tasks.models import Task, TaskAttachment, TaskStatus

from .conftest import login_client, pdf_upload


def _stepped_up(c, user):
    session = c.session
    session["step_up_at"] = int(time.time())
    session["step_up_cv"] = user.credential_version
    session.save()
    return c


def _owner_membership(owner):
    return Membership.objects.get(user=owner)


# ---------- closing ----------

def test_closing_runs_to_the_end_of_the_paid_period(owner, organization, subscription):
    org = closure.request_closure(organization, _owner_membership(owner))
    subscription.refresh_from_db()
    assert subscription.cancel_at_period_end  # nothing more is charged
    assert org.closes_at == subscription.period_end and org.closed_at is None and org.closure_stopped_renewal
    assert AuditEvent.objects.filter(action="account.closure_requested").exists()
    # The job closes it only once the paid period has ended.
    assert closure.close_due(timezone.now()) == 0
    assert closure.close_due(subscription.period_end + timedelta(minutes=1)) == 1
    org.refresh_from_db()
    assert org.closed_at is not None


def test_without_a_paid_plan_it_closes_straight_away(owner, organization):
    org = closure.request_closure(organization, _owner_membership(owner))
    assert org.closed_at is not None


def test_changing_your_mind_restarts_the_renewal(owner, organization, subscription):
    m = _owner_membership(owner)
    closure.request_closure(organization, m)
    with pytest.raises(ValidationFailure):
        renewals.undo_cancel(organization, m)  # must cancel the closure, not just the cancellation
    closure.cancel_closure(organization, m)
    subscription.refresh_from_db()
    organization.refresh_from_db()
    assert not subscription.cancel_at_period_end and organization.closes_at is None


def test_a_plan_already_set_to_end_stays_ended_if_you_change_your_mind(owner, organization, subscription):
    m = _owner_membership(owner)
    renewals.cancel_renewal(organization, m)
    closure.request_closure(organization, m)
    closure.cancel_closure(organization, m)
    subscription.refresh_from_db()
    assert subscription.cancel_at_period_end


def test_closing_with_an_unpaid_renewal_ends_the_plan_today_owing_nothing(owner, organization, subscription):
    subscription.period_end = timezone.now() - timedelta(days=1)  # in the grace period
    subscription.save()
    org = closure.request_closure(organization, _owner_membership(owner))
    assert org.closed_at is not None
    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.CANCELLED


def test_a_paused_account_can_still_close(owner, organization, subscription):
    subscription.period_end = timezone.now() - timedelta(days=5)
    subscription.status = SubscriptionStatus.PAST_DUE
    subscription.save()
    assert closure.request_closure(organization, _owner_membership(owner)).closed_at is not None


def test_only_the_owner_can_close(member, organization):
    with pytest.raises(PermissionFailure):
        closure.request_closure(organization, Membership.objects.get(user=member))
    assert login_client(member).get("/app/security/close/").status_code == 404


def test_close_page_needs_confirmation_and_a_tick(client, owner, organization, subscription):
    assert client.get("/app/security/close/")["Location"].startswith("/step-up/")
    _stepped_up(client, owner)
    body = client.get("/app/security/close/").content.decode()
    assert "Close the" in body and subscription.period_end.strftime("%-d %b %Y") in body
    assert client.post("/app/security/close/", {}).status_code == 400
    r = client.post("/app/security/close/", {"confirm": "yes"})
    assert r.status_code == 302
    organization.refresh_from_db()
    assert organization.is_closing
    assert "Keep my account" in client.get("/app/security/").content.decode()


def test_no_new_plan_while_closing(owner, organization, settings):
    from apps.billing import services as billing

    settings.PAYMENTS_ENABLED = True
    Organization.objects.filter(pk=organization.pk).update(closure_requested_at=timezone.now(),
                                                           closes_at=timezone.now() + timedelta(days=3))
    with pytest.raises(ValidationFailure):
        billing.start_plan_checkout(organization=organization, membership=_owner_membership(owner),
                                    plan_slug="starter", return_url="https://x.example/")


def test_a_closed_account_is_read_only(client, owner, organization, submitted_task):
    closure.request_closure(organization, _owner_membership(owner))
    assert client.get("/app/").status_code == 200  # can still sign in and look
    assert client.get(f"/app/tasks/{submitted_task.pk}/").status_code == 200
    r = client.post("/app/support/new/", {"subject": "Hello", "body": "Can I?"})
    assert r.status_code == 302 and r["Location"] == "/app/"
    assert not SupportTicket.objects.exists()


# ---------- retention ----------

def test_profile_is_deleted_90_days_after_closing(owner, member, organization, other_organization):
    Membership.objects.create(organization=other_organization, user=member, role="member")
    closure.request_closure(organization, _owner_membership(owner))
    organization.refresh_from_db()
    UserSession.objects.create(user=owner, session_key="k" * 40)
    invoice_count = Invoice.objects.count()

    assert retention.delete_closed_profiles(organization.closed_at + timedelta(days=89)) == 0
    assert retention.delete_closed_profiles(organization.closed_at + timedelta(days=91)) == 1
    owner.refresh_from_db()
    member.refresh_from_db()
    organization.refresh_from_db()
    assert organization.name == "Closed business" and organization.profile_deleted_at
    assert owner.email.endswith("@deleted.invalid") and not owner.is_active and owner.full_name == ""
    assert not UserSession.objects.filter(user=owner).exists()
    assert member.is_active and member.email == "member@example.test"  # still in another open business
    assert Invoice.objects.count() == invoice_count  # invoices are kept 7 years
    event = AuditEvent.objects.get(action="retention.profile_deleted")
    assert event.metadata == {"people": 1}
    # Running again changes nothing.
    assert retention.delete_closed_profiles(organization.closed_at + timedelta(days=200)) == 0


def test_legal_hold_stops_deletion(owner, organization):
    closure.request_closure(organization, _owner_membership(owner))
    Organization.objects.filter(pk=organization.pk).update(legal_hold=True)
    organization.refresh_from_db()
    assert retention.delete_closed_profiles(organization.closed_at + timedelta(days=365)) == 0
    owner.refresh_from_db()
    assert owner.is_active


def test_task_documents_go_6_years_after_the_task_closes(owner, organization, make_task, ops_user, settings, tmp_path):
    from apps.files.services import store_upload
    from apps.files.storage import get_storage

    settings.PRIVATE_STORAGE_ROOT = str(tmp_path)
    task = make_task(organization, owner)
    f = store_upload(uploaded_file=pdf_upload(), organization=organization, user=owner)
    TaskAttachment.objects.create(task=task, file=f, added_by=owner)
    TaskMessage.objects.create(task=task, organization=organization, author=owner, author_role="client", body="Here")
    closed = timezone.now()
    Task.objects.filter(pk=task.pk).update(status=TaskStatus.COMPLETED, completed_at=closed)

    assert retention.purge_task_documents(closed + timedelta(days=365 * 5)) == 0
    assert retention.purge_task_documents(closed + timedelta(days=365 * 6 + 2)) == 1
    f.refresh_from_db()
    task.refresh_from_db()
    assert f.purged_at and not get_storage().exists(f.storage_key)
    assert task.brief == "" and not TaskMessage.objects.filter(task=task).exists()
    assert task.reference  # the record of the work stays
    assert retention.purge_task_documents(closed + timedelta(days=365 * 7)) == 0


def test_support_threads_go_after_6_years(owner, organization):
    old = timezone.now() - timedelta(days=365 * 6 + 3)
    SupportTicket.objects.create(reference="SUP-1", organization=organization, created_by=owner, subject="Old",
                                 status=SupportTicketStatus.RESOLVED, resolved_at=old)
    SupportTicket.objects.create(reference="SUP-2", organization=organization, created_by=owner, subject="Open",
                                 status=SupportTicketStatus.OPEN, last_message_at=old)
    assert retention.purge_support(timezone.now()) == 1
    assert list(SupportTicket.objects.values_list("reference", flat=True)) == ["SUP-2"]


def test_the_command_runs(db, capsys):
    from django.core.management import call_command

    call_command("run_retention")
    assert "profiles deleted: 0" in capsys.readouterr().out


def test_consent_evidence_survives_profile_deletion(owner, organization):
    from apps.accounts import profile

    profile.record_authority(owner, organization)
    closure.request_closure(organization, _owner_membership(owner))
    organization.refresh_from_db()
    retention.delete_closed_profiles(organization.closed_at + timedelta(days=91))
    assert ConsentRecord.objects.filter(user=owner).exists()
    assert User.objects.filter(pk=owner.pk).exists()


def test_legal_hold_changes_are_recorded_and_closure_dates_are_read_only(organization, db):
    from django.contrib.admin.sites import site

    admin = site._registry[Organization]
    assert {"closed_at", "closes_at", "profile_deleted_at"} <= set(admin.readonly_fields)
    from .conftest import make_user

    boss = make_user("boss@example.test", is_staff=True, is_superuser=True, mfa=True)
    c = login_client(boss)
    url = f"/admin/organizations/organization/{organization.pk}/change/"
    form = {"name": organization.name, "slug": organization.slug, "status": organization.status,
            "country": organization.country, "legal_hold": "on"}
    assert c.post(url, form).status_code == 302
    organization.refresh_from_db()
    assert organization.legal_hold
    assert AuditEvent.objects.get(action="organization.legal_hold_changed").metadata == {"to": "on"}
