"""Step 5b: data requests and "download my data" (Nigeria Data Protection Act)."""
from __future__ import annotations

import json
import time
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts import privacy
from apps.accounts.models import DataRequest, DataRequestKind, DataRequestStatus
from apps.audit.models import AuditEvent
from apps.communications.models import Notification, TaskMessage
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.core.models import OutboxEvent

from .conftest import login_client, make_user


def _stepped_up(c, user):
    session = c.session
    session["step_up_at"] = int(time.time())
    session["step_up_cv"] = user.credential_version
    session.save()
    return c


# ---------- download my data ----------

def test_download_needs_you_to_confirm_first(client):
    r = client.get("/app/security/data/download/")
    assert r.status_code == 302 and r["Location"].startswith("/step-up/")


def test_download_has_your_data_and_not_staff_notes(client, owner, organization, submitted_task, ops_user):
    TaskMessage.objects.create(task=submitted_task, organization=organization, author=ops_user,
                               author_role="staff", body="INTERNAL ONLY", is_internal=True)
    TaskMessage.objects.create(task=submitted_task, organization=organization, author=ops_user,
                               author_role="staff", body="Could you send the brand colours?")
    r = _stepped_up(client, owner).get("/app/security/data/download/")
    assert r.status_code == 200 and r["Content-Disposition"].startswith("attachment;")
    assert r["Cache-Control"] == "no-store"
    data = json.loads(r.content)
    assert data["account"]["email"] == owner.email
    (business,) = data["businesses"]
    assert business["name"] == organization.name and "invoices" in business
    (task,) = business["requests"]
    bodies = [m["message"] for m in task["messages"]]
    assert "Could you send the brand colours?" in bodies and "INTERNAL ONLY" not in r.content.decode()
    assert AuditEvent.objects.filter(action="privacy.data_downloaded", actor=owner).exists()


def test_members_get_no_invoices_and_nothing_from_other_businesses(member, organization, other_owner):
    data = privacy.export_data(member)
    (business,) = data["businesses"]
    assert "invoices" not in business and business["name"] == organization.name


def test_staff_have_no_client_download(staff_client):
    assert staff_client.get("/app/security/data/download/").status_code == 404
    assert staff_client.get("/app/security/data/request/").status_code == 404


# ---------- data requests ----------

def test_making_a_request(client, owner, organization):
    r = client.post("/app/security/data/request/", {"kind": "correct", "details": "My business address is wrong."})
    assert r.status_code == 302
    req = DataRequest.objects.get(user=owner)
    assert req.reference.startswith("DR-") and req.organization == organization
    assert timedelta(days=29) < req.reply_by - timezone.now() <= timedelta(days=30)
    email = OutboxEvent.objects.get(topic=OutboxEvent.Topic.EMAIL)
    assert email.payload["to"] == ["dpo@bandaessentials.com"]
    assert "address is wrong" not in email.payload["body"]  # the details stay in the app
    assert AuditEvent.objects.get(action="privacy.request_made").metadata == {"kind": "correct"}
    assert req.reference in client.get("/app/security/").content.decode()


def test_details_are_needed_for_some_requests(client, owner):
    r = client.post("/app/security/data/request/", {"kind": "correct", "details": " "})
    assert r.status_code == 400 and "Tell us a little more" in r.content.decode()
    assert client.post("/app/security/data/request/", {"kind": "delete"}).status_code == 302
    assert client.post("/app/security/data/request/", {"kind": "nonsense"}).status_code == 400


def test_open_requests_are_capped(owner):
    for _ in range(privacy.MAX_OPEN):
        privacy.make_request(owner, DataRequestKind.COPY)
    with pytest.raises(ValidationFailure):
        privacy.make_request(owner, DataRequestKind.COPY)


def test_answering_a_request(owner, db):
    dpo = make_user("dpo@example.test", is_staff=True, is_superuser=True)
    req = privacy.make_request(owner, DataRequestKind.DELETE)
    with pytest.raises(ValidationFailure):
        privacy.resolve(req, dpo, status=DataRequestStatus.DECLINED, outcome="")
    privacy.resolve(req, dpo, status=DataRequestStatus.DONE, outcome="Deleted your phone number.")
    req.refresh_from_db()
    assert req.status == DataRequestStatus.DONE and req.resolved_by == dpo
    assert Notification.objects.filter(user=owner, title__contains=req.reference).exists()
    event = AuditEvent.objects.get(action="privacy.request_answered")
    assert event.metadata == {"kind": "delete", "status": "done", "late": "no"}
    with pytest.raises(ValidationFailure):
        privacy.resolve(req, dpo, status=DataRequestStatus.DONE, outcome="again")


def test_only_permitted_staff_can_answer(owner, ops_user):
    req = privacy.make_request(owner, DataRequestKind.COPY)
    with pytest.raises(PermissionFailure):
        privacy.resolve(req, ops_user, status=DataRequestStatus.DONE, outcome="")


def test_admin_answer_form_needs_a_reason_to_decline(owner, db):

    dpo = make_user("dpo@example.test", is_staff=True, is_superuser=True, mfa=True)
    req = privacy.make_request(owner, DataRequestKind.DELETE)
    c = login_client(dpo)
    url = f"/admin/accounts/datarequest/{req.pk}/change/"
    r = c.post(url, {"status": "declined", "outcome": ""})
    assert r.status_code == 200 and "Explain why" in r.content.decode()
    c.post(url, {"status": "declined", "outcome": "We must keep invoices for 7 years by law."})
    req.refresh_from_db()
    assert req.status == "declined" and req.resolved_by == dpo
    # Once answered, the answer can't be changed.
    c.post(url, {"status": "done", "outcome": "changed"})
    req.refresh_from_db()
    assert req.status == "declined"
