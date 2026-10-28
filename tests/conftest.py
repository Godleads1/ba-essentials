from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.accounts.models import StaffRole, User
from apps.billing.models import Plan, Subscription, SubscriptionStatus
from apps.catalogue.models import (
    ServiceCategory,
    ServiceDefinition,
    ServiceState,
    ServiceVersion,
)
from apps.organizations.models import Membership, MembershipRole, Organization
from apps.tasks import services as task_services
from apps.tasks.models import Task, TaskStatus
from apps.tasks.transitions import Action, transition

PASSWORD = "Synthetic-Pass-2026!"


def make_user(email: str, *, staff_role: str = "", mfa: bool = False, **extra) -> User:
    """Create a verified synthetic user. No real personal data is used."""
    extra.setdefault("full_name", "Synthetic Person")
    extra.setdefault("email_verified_at", timezone.now())
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        staff_role=staff_role,
        mfa_enabled=mfa,
        **extra,
    )


@pytest.fixture
def organization(db) -> Organization:
    return Organization.objects.create(name="Synthetic Studio", slug="synthetic-studio")


@pytest.fixture
def other_organization(db) -> Organization:
    return Organization.objects.create(name="Other Tenant", slug="other-tenant")


@pytest.fixture
def owner(organization) -> User:
    user = make_user("owner@example.test")
    Membership.objects.create(
        organization=organization, user=user, role=MembershipRole.OWNER
    )
    return user


@pytest.fixture
def member(organization) -> User:
    user = make_user("member@example.test")
    Membership.objects.create(
        organization=organization, user=user, role=MembershipRole.MEMBER
    )
    return user


@pytest.fixture
def other_owner(other_organization) -> User:
    user = make_user("owner@other.test")
    Membership.objects.create(
        organization=other_organization, user=user, role=MembershipRole.OWNER
    )
    return user


@pytest.fixture
def ops_user(db) -> User:
    return make_user(
        "ops@example.test", staff_role=StaffRole.OPERATIONS, mfa=True, is_staff=True
    )


@pytest.fixture
def quote_approver(db) -> User:
    return make_user(
        "quotes@example.test",
        staff_role=StaffRole.QUOTE_APPROVER,
        mfa=True,
        is_staff=True,
    )


@pytest.fixture
def specialist(db) -> User:
    return make_user(
        "specialist@example.test",
        staff_role=StaffRole.SPECIALIST,
        mfa=True,
        is_staff=True,
    )


@pytest.fixture
def reviewer(db) -> User:
    return make_user(
        "reviewer@example.test", staff_role=StaffRole.REVIEWER, mfa=True, is_staff=True
    )


@pytest.fixture
def finance(db) -> User:
    return make_user(
        "finance@example.test", staff_role=StaffRole.FINANCE, mfa=True, is_staff=True
    )


@pytest.fixture
def service_version(db) -> ServiceVersion:
    category = ServiceCategory.objects.create(
        name="Brand & Design", slug="brand-design", summary="Synthetic category"
    )
    definition = ServiceDefinition.objects.create(
        category=category, name="Logo Refresh", slug="logo-refresh"
    )
    return ServiceVersion.objects.create(
        definition=definition,
        version_number=1,
        scope_summary="Refresh an existing logo for digital use.",
        deliverables_summary="PNG and SVG logo files with a short usage note.",
        turnaround_days=5,
        revision_allowance=1,
        state=ServiceState.AVAILABLE,
        is_current=True,
    )


@pytest.fixture
def plan(db) -> Plan:
    return Plan.objects.create(
        name="Starter", slug="starter", monthly_task_allowance=2, price_minor=0
    )


@pytest.fixture
def subscription(organization, plan) -> Subscription:
    now = timezone.now()
    return Subscription.objects.create(
        organization=organization,
        plan=plan,
        period_start=now - timedelta(days=1),
        period_end=now + timedelta(days=29),
        status=SubscriptionStatus.ACTIVE,
    )


def pdf_upload(name: str = "brief.pdf", body: bytes = b"%PDF-1.4\n% synthetic\n"):
    return SimpleUploadedFile(name, body, content_type="application/pdf")


@pytest.fixture
def make_task(service_version):
    def _make(organization, user, **overrides) -> Task:
        params = {
            "organization": organization,
            "user": user,
            "service_version": service_version,
            "title": "Refresh the launch logo",
            "brief": "We need a refreshed logo for the new product launch.",
            "desired_outcome": "A final logo pack ready for print and web.",
        }
        params.update(overrides)
        return task_services.create_request(**params)

    return _make


@pytest.fixture
def submitted_task(make_task, organization, owner) -> Task:
    return make_task(organization, owner)


@pytest.fixture
def scoping_task(submitted_task, ops_user, quote_approver) -> Task:
    task = transition(task=submitted_task, action=Action.TRIAGE, actor=ops_user)
    task.refresh_from_db()
    task = transition(task=task, action=Action.SCOPE, actor=quote_approver)
    task.refresh_from_db()
    assert task.status == TaskStatus.SCOPING
    return task


def login_client(user):
    """Force-login a client and stamp the credential version the middleware expects."""
    from django.test import Client

    import time

    c = Client()
    c.force_login(user)
    session = c.session
    session["cv"] = user.credential_version
    session["last_seen"] = int(time.time())
    session.save()
    return c


@pytest.fixture
def client(owner):
    return login_client(owner)


@pytest.fixture
def other_client(other_owner):
    return login_client(other_owner)


@pytest.fixture
def staff_client(ops_user):
    return login_client(ops_user)
