"""Step 6b: people see their own activity; owners see their business's, staff shown by role."""
from __future__ import annotations

from apps.accounts.models import StaffRole
from apps.audit import services as audit_service
from apps.audit.activity import business_activity, own_activity

from .conftest import login_client, make_user


def test_your_own_security_activity_is_on_profile_and_security(client, owner):
    audit_service.record(action="auth.login", actor=owner, resource_type="user", resource_id=str(owner.pk))
    audit_service.record(action="auth.code_channel_changed", actor=owner, metadata={"channel": "whatsapp"})
    audit_service.record(action="message.posted", actor=owner)  # not a security event
    rows = own_activity(owner)
    assert [r["what"] for r in rows] == ["Changed how codes are sent", "Signed in"]
    assert rows[0]["detail"] == "by WhatsApp"
    body = client.get("/app/security/").content.decode()
    assert "Recent activity" in body and "Signed in" in body


def test_you_never_see_someone_elses_activity(owner, other_owner):
    audit_service.record(action="auth.login", actor=other_owner)
    assert own_activity(owner) == []


def test_owners_see_staff_opening_their_requests_by_role_not_name(client, owner, organization, ops_user,
                                                                    submitted_task):
    ops_user.full_name = "Olu Staffer"
    ops_user.save()
    login_client(ops_user).get(f"/staff/tasks/{submitted_task.pk}/")
    rows = business_activity(organization, owner)
    staff_row = next(r for r in rows if r["what"] == "opened request")
    assert staff_row["who"] == "B & A team · Operations" and staff_row["target"] == submitted_task.reference
    body = client.get("/app/activity/").content.decode()
    assert "B &amp; A team · Operations" in body and submitted_task.reference in body
    assert ops_user.email not in body and "Olu Staffer" not in body


def test_staff_list_pages_that_name_many_clients_are_not_shown_to_one_client(owner, organization, ops_user):
    audit_service.record(action="staff.list_viewed", actor=ops_user, organization=organization,
                         resource_type="page", resource_id="queue")
    assert business_activity(organization, owner) == []


def test_other_businesses_activity_is_not_shown(owner, organization, other_owner, other_organization):
    audit_service.record(action="message.posted", actor=other_owner, organization=other_organization,
                         resource_type="task", resource_id="BAE-9")
    assert business_activity(organization, owner) == []


def test_only_owners_can_open_business_activity(member, staff_client):
    assert login_client(member).get("/app/activity/").status_code == 404
    assert staff_client.get("/app/activity/").status_code == 404


def test_admin_audit_log_filters_by_date(db):
    admin = make_user("admin6b@example.test", staff_role=StaffRole.ADMIN, mfa=True)
    from datetime import UTC, datetime
    from unittest import mock

    # Audit events can't be edited afterwards, so this one is written "on" 5 January.
    with mock.patch("django.utils.timezone.now", return_value=datetime(2026, 1, 5, 10, tzinfo=UTC)):
        audit_service.record(action="auth.login", actor=admin)
    c = login_client(admin)
    body = c.get("/staff/audit/?from=2026-01-01&to=2026-01-31&action=auth.login").content.decode()
    assert "auth.login" in body and "5 Jan 2026" in body
    body = c.get("/staff/audit/?from=2026-02-01&action=auth.login").content.decode()
    assert "5 Jan 2026" not in body
    assert c.get("/staff/audit/?from=nonsense").status_code == 200
