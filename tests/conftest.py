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
    user = User.objects.create_user(
        email=email,
        password=PASSWORD,
        staff_role=staff_role,
        mfa_enabled=mfa,
        **extra,
    )
    if not staff_role:
        # Clients agree to the current Terms when they sign up (stored without an activity entry,
        # so tests about the activity log only see what they do themselves).
        from apps.accounts.models import ConsentKind, ConsentRecord
        from apps.accounts.profile import AGREEMENT_WORDING
        from apps.core.legal import AGREEMENT_VERSION

        ConsentRecord.objects.create(user=user, kind=ConsentKind.AGREEMENT, granted=True,
                                     wording=AGREEMENT_WORDING, version=AGREEMENT_VERSION)
    return user


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
    # Staff confirm where the work is for during scoping, before any quote.
    from apps.tasks.services import confirm_work_country

    task = confirm_work_country(task, quote_approver, task.work_country or "NG")
    return task


def login_client(user):
    """Force-login a client and stamp the credential version the middleware expects."""
    import time

    from django.test import Client

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


class FakePaystack:
    """Stands in for Paystack's API. Tests set `verified` to what Paystack would say."""

    def __init__(self):
        self.calls = []
        self.checkout_url = "https://checkout.paystack.com/synthetic-access-code"
        self.verified = {}
        self.fail_with = None
        self.charge_status = "success"

    def __call__(self, method, path, payload=None):
        from apps.billing import paystack

        self.calls.append((method, path, payload))
        if self.fail_with is not None:
            raise self.fail_with
        if path == "/transaction/initialize":
            return {"authorization_url": self.checkout_url, "reference": payload["reference"]}
        if path.startswith("/transaction/verify/"):
            reference = path.rsplit("/", 1)[1]
            if reference not in self.verified:
                raise paystack.PaystackError("Transaction not found.")
            return self.verified[reference]
        if path == "/transaction/charge_authorization":
            if self.charge_status == "error":
                raise paystack.PaystackError("Declined.")
            return {"status": self.charge_status, "reference": payload["reference"]}
        raise AssertionError(f"Unexpected Paystack call {method} {path}")

    def confirm(self, reference, amount_minor, *, currency="NGN", status="success", authorization=None):
        self.verified[reference] = {
            "status": status,
            "reference": reference,
            "amount": amount_minor,
            "currency": currency,
            "customer": {"email": "owner@example.test"},
            "authorization": authorization or {},
        }


class FakeFlutterwave:
    """Stands in for Flutterwave's API (amounts in major units, as Flutterwave sends them)."""

    def __init__(self):
        self.calls = []
        self.checkout_url = "https://checkout.flutterwave.com/v3/hosted/pay/synthetic"
        self.verified = {}
        self.rate = "1550.00"
        self.fail_with = None

    def __call__(self, method, path, payload=None):
        from apps.billing import flutterwave

        self.calls.append((method, path, payload))
        if self.fail_with is not None:
            raise self.fail_with
        if path == "/payments":
            return {"link": self.checkout_url}
        if path.startswith("/transactions/verify_by_reference"):
            reference = path.split("tx_ref=", 1)[1]
            if reference not in self.verified:
                raise flutterwave.FlutterwaveError("Transaction not found.")
            return self.verified[reference]
        if path == "/tokenized-charges":
            return {"status": getattr(self, "charge_status", "successful"), "tx_ref": payload["tx_ref"]}
        if path.startswith("/transfers/rates"):
            # The app must ask for US dollars to naira.
            assert "source_currency=USD" in path and "destination_currency=NGN" in path, path
            if self.rate is None:
                raise flutterwave.FlutterwaveUnavailable("down")
            return {"rate": self.rate}
        raise AssertionError(f"Unexpected Flutterwave call {method} {path}")

    def confirm(self, reference, amount_minor, *, currency="USD", status="successful", country="GB", fee_minor=None, card=None):
        self.verified[reference] = {
            "status": status,
            "tx_ref": reference,
            "amount": amount_minor / 100,
            "currency": currency,
            "customer": {"email": "owner@example.test"},
            "card": {"country": country, **(card or {})},
            "app_fee": None if fee_minor is None else fee_minor / 100,
        }


@pytest.fixture(autouse=True)
def fake_flutterwave(monkeypatch):
    """No test ever reaches the real Flutterwave API, and each starts without a cached rate."""
    from django.core.cache import cache

    from apps.billing import flutterwave

    fake = FakeFlutterwave()
    fake.original = flutterwave._request  # for tests of the client's own error handling
    monkeypatch.setattr(flutterwave, "_request", fake)
    cache.clear()
    return fake


@pytest.fixture(autouse=True)
def fake_paystack(monkeypatch):
    """No test ever reaches the real Paystack API."""
    from apps.billing import paystack

    fake = FakePaystack()
    monkeypatch.setattr(paystack, "_request", fake)
    return fake


class FakeTermii:
    """Records what would be sent by SMS or WhatsApp. No test reaches Termii."""

    def __init__(self):
        self.sent = []
        self.fail = False

    def __call__(self, payload):
        from apps.accounts.termii import TermiiError

        if self.fail:
            raise TermiiError("Termii could not be reached.")
        self.sent.append(payload)
        return {"code": "ok", "message_id": str(len(self.sent))}

    def last_code(self):
        import re

        return re.search(r"\b(\d{6})\b", self.sent[-1]["sms"]).group(1)


@pytest.fixture(autouse=True)
def fake_termii(monkeypatch):
    from apps.accounts import termii

    fake = FakeTermii()
    monkeypatch.setattr(termii, "_request", fake)
    return fake
