"""Dedicated clients get a named account manager (pricing page promise).

Operations or an Admin name one on the staff Clients page; the client sees them on
the dashboard; new support tickets from the business go to them first.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import StaffRole
from apps.audit.models import AuditEvent
from apps.billing.models import Plan, Subscription, SubscriptionStatus
from apps.communications import services as communications
from apps.communications.models import Notification
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.organizations import account_manager

from .conftest import login_client, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def support_user(db):
    return make_user("support@example.test", staff_role=StaffRole.SUPPORT, mfa=True, is_staff=True,
                     full_name="Ada Support")


@pytest.fixture
def dedicated(organization):
    plan = Plan.objects.create(name="Dedicated", slug="dedicated", rank=3, monthly_task_allowance=30,
                               price_minor=26_000_000)
    now = timezone.now()
    return Subscription.objects.create(organization=organization, plan=plan, status=SubscriptionStatus.ACTIVE,
                                       period_start=now - timedelta(days=1), period_end=now + timedelta(days=29))


def _set(client, organization, manager_id):
    return client.post(f"/staff/clients/{organization.pk}/account-manager/", {"manager": str(manager_id or "")})


# ---------- who can name one ----------

def test_operations_can_name_and_remove_a_manager(staff_client, ops_user, organization, support_user):
    response = _set(staff_client, organization, support_user.pk)
    assert response.status_code == 302
    organization.refresh_from_db()
    assert organization.account_manager == support_user and organization.account_manager_since
    event = AuditEvent.objects.get(action="client.account_manager_set")
    assert event.actor_id == ops_user.pk and event.metadata["manager_id"] == str(support_user.pk)
    assert Notification.objects.filter(user=support_user).exists()

    _set(staff_client, organization, None)
    organization.refresh_from_db()
    assert organization.account_manager is None and organization.account_manager_since is None


@pytest.mark.parametrize("role", [StaffRole.SUPPORT, StaffRole.FINANCE, StaffRole.SPECIALIST, StaffRole.REVIEWER])
def test_other_roles_cannot_name_one(role, organization, support_user):
    actor = make_user(f"actor-{role}@example.test", staff_role=role, mfa=True, is_staff=True)
    _set(login_client(actor), organization, support_user.pk)
    organization.refresh_from_db()
    assert organization.account_manager is None
    with pytest.raises(PermissionFailure):
        account_manager.set_account_manager(actor=actor, organization=organization, manager=support_user)


def test_clients_cannot_reach_the_staff_action(client, organization, support_user):
    response = _set(client, organization, support_user.pk)
    assert response.status_code in (302, 403, 404)
    organization.refresh_from_db()
    assert organization.account_manager is None


def test_only_active_support_capable_staff_can_be_named(ops_user, organization, specialist, owner, support_user):
    for not_allowed in (specialist, owner):
        with pytest.raises(ValidationFailure):
            account_manager.set_account_manager(actor=ops_user, organization=organization, manager=not_allowed)
    support_user.is_active = False
    support_user.save(update_fields=["is_active"])
    with pytest.raises(ValidationFailure):
        account_manager.set_account_manager(actor=ops_user, organization=organization, manager=support_user)


def test_a_forged_manager_id_is_refused(staff_client, organization, specialist):
    _set(staff_client, organization, specialist.pk)
    _set(staff_client, organization, "not-a-uuid")
    organization.refresh_from_db()
    assert organization.account_manager is None


def test_setting_the_same_manager_twice_records_once(ops_user, organization, support_user):
    for _ in range(2):
        account_manager.set_account_manager(actor=ops_user, organization=organization, manager=support_user)
    assert AuditEvent.objects.filter(action="client.account_manager_set").count() == 1


# ---------- what the client sees ----------

def test_dedicated_client_sees_their_manager(client, organization, dedicated, ops_user, support_user):
    account_manager.set_account_manager(actor=ops_user, organization=organization, manager=support_user)
    page = client.get("/app/").content.decode()
    assert "Your account manager" in page and "Ada Support" in page


def test_dedicated_without_a_manager_is_told_one_is_coming(client, organization, dedicated):
    page = client.get("/app/").content.decode()
    assert "naming your account manager" in page


def test_a_manager_who_loses_access_disappears(client, organization, dedicated, ops_user, support_user):
    account_manager.set_account_manager(actor=ops_user, organization=organization, manager=support_user)
    support_user.is_active = False
    support_user.save(update_fields=["is_active"])
    page = client.get("/app/").content.decode()
    assert "Ada Support" not in page and "naming your account manager" in page


def test_another_business_never_sees_the_manager(other_client, organization, dedicated, ops_user, support_user):
    account_manager.set_account_manager(actor=ops_user, organization=organization, manager=support_user)
    assert "Ada Support" not in other_client.get("/app/").content.decode()


# ---------- support routing ----------

def test_new_tickets_go_to_the_manager_first(organization, owner, ops_user, support_user):
    account_manager.set_account_manager(actor=ops_user, organization=organization, manager=support_user)
    ticket = communications.create_ticket(organization=organization, author=owner,
                                          subject="Question about my plan", body="Hello")
    assert ticket.assigned_to == support_user
    assert Notification.objects.filter(user=support_user, ticket=ticket).exists()
    assert Notification.objects.filter(user=ops_user, ticket=ticket).exists()  # support still hears


def test_without_a_manager_tickets_stay_unassigned(organization, owner):
    ticket = communications.create_ticket(organization=organization, author=owner,
                                          subject="Question about my plan", body="Hello")
    assert ticket.assigned_to is None


# ---------- staff Clients page ----------

def test_clients_page_flags_dedicated_without_a_manager(staff_client, organization, dedicated):
    page = staff_client.get("/staff/clients/").content.decode()
    assert "Dedicated" in page and "Dedicated: needs a manager" in page


def test_support_sees_the_list_but_no_controls(organization, support_user):
    page = login_client(support_user).get("/staff/clients/").content.decode()
    assert organization.name in page and "account-manager/" not in page
