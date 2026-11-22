"""The staff Team screen (apps/accounts/team.py): invitations, roles, removing access, and the
second-Admin approval for powerful roles."""
from __future__ import annotations

import re
import time

import pytest
from django.test import Client

from apps.accounts import team
from apps.accounts.models import StaffAccessRequest, StaffRequestStatus, StaffRole, User
from apps.audit.models import AuditEvent
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.core.models import OutboxEvent

from .conftest import login_client, make_user

NEW_PASSWORD = "Copper-Meadow-Lantern-42"  # gitleaks:allow (a made-up test password)


def _admin(email):
    return make_user(email, staff_role=StaffRole.ADMIN, mfa=True, is_staff=True)


@pytest.fixture
def admin_a(db):
    return _admin("admin.a@example.test")


@pytest.fixture
def admin_b(db):
    return _admin("admin.b@example.test")


def _confirmed(user) -> Client:
    """Signed in, with a fresh "confirm it's you"."""
    c = login_client(user)
    session = c.session
    session["step_up_at"], session["step_up_cv"] = int(time.time()), user.credential_version
    session.save()
    return c


def _emails_to(address):
    return [e.payload for e in OutboxEvent.objects.all() if address in e.payload.get("to", [])]


def _join_link(address):
    (mail,) = [p for p in _emails_to(address) if "invited" in p["subject"]]
    return re.search(r"https?://[^/\s]+(/staff/join/[^/\s]+/)", mail["body"]).group(1)


# ---------- who can use it ----------

def test_only_admins_see_and_use_the_team_screen(admin_a, ops_user):
    assert "Team" in login_client(admin_a).get("/staff/").content.decode()
    assert login_client(admin_a).get("/staff/team/").status_code == 200
    ops = _confirmed(ops_user)
    assert ">Team<" not in ops.get("/staff/").content.decode()
    assert ops.get("/staff/team/").status_code == 302
    ops.post("/staff/team/invite/", {"email": "x@example.test", "role": "admin"})
    assert not StaffAccessRequest.objects.exists()
    with pytest.raises(PermissionFailure):
        team.invite(ops_user, "x@example.test", "support")
    assert Client().get("/staff/team/").status_code == 302


def test_changes_need_a_fresh_confirm_its_you(admin_a):
    r = login_client(admin_a).post("/staff/team/invite/", {"email": "new@example.test", "role": "support"})
    assert r.status_code == 302 and r["Location"].startswith("/step-up/")
    assert not StaffAccessRequest.objects.exists()


# ---------- ordinary roles: at once ----------

def test_inviting_an_ordinary_role_sends_the_link_at_once(admin_a):
    _confirmed(admin_a).post("/staff/team/invite/", {"email": "New.Support@Example.test", "role": "support"})
    req = StaffAccessRequest.objects.get()
    assert req.status == StaffRequestStatus.INVITED and req.email == "new.support@example.test"
    link = _join_link("new.support@example.test")
    assert link.split("/")[-2] not in (req.token_hash or "")  # only the hash is stored
    page = Client().get(link)
    assert page.status_code == 200 and "Support" in page.content.decode()


def test_accepting_creates_a_staff_account_that_must_set_up_two_factor(admin_a):
    team.invite(admin_a, "joiner@example.test", StaffRole.SUPPORT)
    link = _join_link("joiner@example.test")
    c = Client()
    r = c.post(link, {"full_name": "Jo Iner", "password": NEW_PASSWORD, "password_again": NEW_PASSWORD})
    assert r.status_code == 302 and r["Location"] == "/login/staff/"
    user = User.objects.get(email="joiner@example.test")
    assert user.staff_role == StaffRole.SUPPORT and not user.is_staff and not user.mfa_enabled
    assert user.check_password(NEW_PASSWORD) and not user.is_client_account
    # The link works once.
    assert Client().get(link).status_code == 404
    # Signing in sends them to set up an authenticator before anything else.
    c.post("/login/staff/", {"email": user.email, "password": NEW_PASSWORD})
    assert c.get("/staff/").status_code == 302


def test_the_join_page_checks_the_password(admin_a, fake_pwned):
    team.invite(admin_a, "joiner@example.test", StaffRole.SUPPORT)
    link = _join_link("joiner@example.test")
    for password, again, message in (("short", "short", "too short"),
                                     (NEW_PASSWORD, NEW_PASSWORD + "x", "don&#x27;t match")):
        body = Client().post(link, {"full_name": "Jo", "password": password, "password_again": again}).content.decode()
        assert message in body
    fake_pwned.breached.add(NEW_PASSWORD)
    body = Client().post(link, {"full_name": "Jo", "password": NEW_PASSWORD, "password_again": NEW_PASSWORD})
    assert "data breach" in body.content.decode()
    assert not User.objects.filter(email="joiner@example.test").exists()


def test_bad_and_expired_links_do_nothing(admin_a):
    from datetime import timedelta

    from django.utils import timezone

    assert Client().get("/staff/join/not-a-real-token/").status_code == 404
    team.invite(admin_a, "late@example.test", StaffRole.SUPPORT)
    link = _join_link("late@example.test")
    StaffAccessRequest.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert Client().get(link).status_code == 404
    with pytest.raises(ValidationFailure):
        team.accept_invitation(link.split("/")[-2], full_name="Late", password=NEW_PASSWORD)


def test_a_client_email_or_a_team_member_cannot_be_invited(admin_a, owner, ops_user):
    with pytest.raises(ValidationFailure, match="client account"):
        team.invite(admin_a, owner.email, StaffRole.SUPPORT)
    with pytest.raises(ValidationFailure, match="already on the team"):
        team.invite(admin_a, ops_user.email, StaffRole.SUPPORT)


def test_cancelling_an_invitation_kills_the_link(admin_a):
    req = team.invite(admin_a, "gone@example.test", StaffRole.SUPPORT)
    link = _join_link("gone@example.test")
    team.reject(admin_a, req.pk)
    assert Client().get(link).status_code == 404


# ---------- powerful roles: a second Admin ----------

@pytest.mark.parametrize("role", [StaffRole.ADMIN, StaffRole.FINANCE, StaffRole.QUOTE_APPROVER])
def test_powerful_invitations_wait_for_a_second_admin(admin_a, admin_b, role):
    req = team.invite(admin_a, "power@example.test", role)
    assert req.status == StaffRequestStatus.AWAITING_APPROVAL and not _emails_to("power@example.test")
    (ask,) = _emails_to(admin_b.email)
    assert "Approval needed" in ask["subject"] and not _emails_to(admin_a.email)
    with pytest.raises(PermissionFailure, match="different Admin"):
        team.approve(admin_a, req.pk)
    team.approve(admin_b, req.pk)
    link = _join_link("power@example.test")
    Client().post(link, {"full_name": "P", "password": NEW_PASSWORD, "password_again": NEW_PASSWORD})
    assert User.objects.get(email="power@example.test").staff_role == role


def test_the_screen_shows_each_admin_what_to_approve(admin_a, admin_b):
    _confirmed(admin_a).post("/staff/team/invite/", {"email": "power@example.test", "role": "finance"})
    mine = _confirmed(admin_a).get("/staff/team/").content.decode()
    assert "Waiting for another Admin" in mine and "Waiting for your approval" not in mine
    theirs = _confirmed(admin_b).get("/staff/team/").content.decode()
    assert "Waiting for your approval" in theirs
    req = StaffAccessRequest.objects.get()
    _confirmed(admin_b).post(f"/staff/team/requests/{req.pk}/approve/")
    req.refresh_from_db()
    assert req.status == StaffRequestStatus.INVITED and req.decided_by == admin_b


def test_moving_someone_into_a_powerful_role_waits(admin_a, admin_b, ops_user):
    req = team.change_role(admin_a, ops_user.pk, StaffRole.FINANCE)
    ops_user.refresh_from_db()
    assert req is not None and ops_user.staff_role == StaffRole.OPERATIONS
    team.approve(admin_b, req.pk)
    ops_user.refresh_from_db()
    assert ops_user.staff_role == StaffRole.FINANCE
    assert AuditEvent.objects.filter(action="staff.access_changed", resource_id=str(ops_user.pk)).exists()


def test_ordinary_role_changes_take_effect_at_once(admin_a, ops_user):
    assert team.change_role(admin_a, ops_user.pk, StaffRole.SUPPORT) is None
    ops_user.refresh_from_db()
    assert ops_user.staff_role == StaffRole.SUPPORT


def test_turning_down_a_request(admin_a, admin_b, ops_user):
    req = team.change_role(admin_a, ops_user.pk, StaffRole.ADMIN)
    team.reject(admin_b, req.pk)
    req.refresh_from_db()
    ops_user.refresh_from_db()
    assert req.status == StaffRequestStatus.REJECTED and ops_user.staff_role == StaffRole.OPERATIONS
    with pytest.raises(ValidationFailure):
        team.approve(admin_b, req.pk)


def test_nobody_approves_a_change_to_themselves(admin_a, admin_b):
    finance = make_user("fin.self@example.test", staff_role=StaffRole.FINANCE, mfa=True)
    req = team.change_role(admin_a, finance.pk, StaffRole.ADMIN)
    # Even made an Admin elsewhere, the person a request is about can't approve it.
    User.objects.filter(pk=finance.pk).update(staff_role=StaffRole.ADMIN)
    finance.refresh_from_db()
    with pytest.raises(PermissionFailure, match="your own access"):
        team.approve(finance, req.pk)


# ---------- removing access and the guard rails ----------

def test_removing_access_is_immediate_and_signs_them_out(admin_a, ops_user):
    c = login_client(ops_user)
    assert c.get("/staff/").status_code == 200
    _confirmed(admin_a).post(f"/staff/team/{ops_user.pk}/remove/")
    ops_user.refresh_from_db()
    assert not ops_user.is_active
    assert c.get("/staff/").status_code == 302
    assert AuditEvent.objects.filter(action="staff.access_removed", actor=admin_a).exists()


def test_restoring_a_powerful_role_needs_approval(admin_a, admin_b, finance):
    team.remove_access(admin_a, finance.pk)
    req = team.restore_access(admin_a, finance.pk)
    finance.refresh_from_db()
    assert req is not None and not finance.is_active
    team.approve(admin_b, req.pk)
    finance.refresh_from_db()
    assert finance.is_active


def test_no_one_changes_themselves_or_a_superuser(admin_a, admin_b):
    superuser = User.objects.create_superuser("root@example.test", "Synthetic-Pass-2026!")
    for action in (lambda: team.change_role(admin_a, admin_a.pk, StaffRole.SUPPORT),
                   lambda: team.remove_access(admin_a, admin_a.pk),
                   lambda: team.change_role(admin_a, superuser.pk, StaffRole.SUPPORT),
                   lambda: team.remove_access(admin_a, superuser.pk)):
        with pytest.raises(PermissionFailure):
            action()


def test_the_last_admin_stays(admin_a):
    # The person making a change is always an active Admin, so through the screen there's always
    # another one; the guard is there in case that ever stops being true.
    with pytest.raises(ValidationFailure, match="only active Admin"):
        team._keeps_an_admin(admin_a)


def test_clients_cannot_be_changed_from_the_team_screen(admin_a, owner):
    with pytest.raises(ValidationFailure, match="isn't on the team"):
        team.change_role(admin_a, owner.pk, StaffRole.ADMIN)


def test_every_step_is_audited(admin_a, admin_b):
    req = team.invite(admin_a, "audit.me@example.test", StaffRole.ADMIN)
    team.approve(admin_b, req.pk)
    link = _join_link("audit.me@example.test")
    Client().post(link, {"full_name": "A", "password": NEW_PASSWORD, "password_again": NEW_PASSWORD})
    actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {"staff.invite_requested", "staff.request_approved", "staff.invitation_sent",
            "staff.invitation_accepted", "staff.access_changed"} <= actions
