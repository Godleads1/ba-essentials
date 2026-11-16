"""Step 5a: a client's details, contact preferences and the agreements they give."""
from __future__ import annotations

import re

import pytest
from django.test import Client

from apps.accounts import profile
from apps.accounts.models import ConsentKind, ConsentRecord, User
from apps.audit.models import AuditEvent
from apps.communications import services as communications
from apps.communications.models import NotificationKind
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.core.legal import AGREEMENT_VERSION
from apps.core.models import OutboxEvent
from apps.organizations.models import Organization

from .conftest import login_client, make_user


def _mail_code(outbox) -> str:
    return re.search(r"\b(\d{6})\b", outbox[-1].body).group(1)


# ---------- agreements at sign-up and set-up ----------

def test_sign_up_records_the_agreement_once_the_email_is_proven(db, mailoutbox):
    c = Client()
    c.post("/signup/", {"full_name": "New Client", "email": "new@example.test", "channel": "email", "accept_terms": "on"})
    user = User.objects.get(email="new@example.test")
    assert not ConsentRecord.objects.filter(user=user).exists()  # nothing until the code is entered
    c.post("/login/code/", {"code": _mail_code(mailoutbox)})
    consent = ConsentRecord.objects.get(user=user)
    assert consent.kind == ConsentKind.AGREEMENT and consent.granted
    assert consent.version == AGREEMENT_VERSION and "18 or over" in consent.wording
    assert AuditEvent.objects.filter(action="account.consent_given", actor=user).exists()


def test_setting_up_a_business_needs_and_records_authority(db):
    user = make_user("founder@example.test")
    c = login_client(user)
    body = c.get("/app/onboarding/").content.decode()
    assert "banda/app.css" in body and "allowed to act for this business" in body
    r = c.post("/app/onboarding/", {"name": "Mensah & Co", "country": "GH"})
    assert r.status_code == 200 and not Organization.objects.filter(name="Mensah & Co").exists()
    c.post("/app/onboarding/", {"name": "Mensah & Co", "country": "GH", "confirm_authority": "on"})
    org = Organization.objects.get(name="Mensah & Co")
    consent = ConsentRecord.objects.get(user=user, kind=ConsentKind.AUTHORITY)
    assert consent.organization == org and "Mensah & Co" in consent.wording


def test_consent_records_cannot_be_edited_in_admin(db, rf):
    from django.contrib.admin.sites import site

    admin = site._registry[ConsentRecord]
    request = rf.get("/")
    assert not admin.has_add_permission(request)
    assert not admin.has_change_permission(request) and not admin.has_delete_permission(request)


# ---------- details ----------

def test_owner_changes_name_and_business(client, owner, organization):
    r = client.post("/app/security/details/", {"full_name": "  Ada   Okafor ", "business_name": "Okafor Foods"})
    assert r.status_code == 302
    owner.refresh_from_db()
    organization.refresh_from_db()
    assert owner.full_name == "Ada Okafor" and organization.name == "Okafor Foods"
    event = AuditEvent.objects.get(action="account.details_changed")
    assert event.metadata == {"fields": "full_name,business_name"}  # names of fields, never values
    assert "Okafor" not in str(event.metadata)


def test_a_member_cannot_rename_the_business(member, organization):
    c = login_client(member)
    body = c.get("/app/security/").content.decode()
    assert "Only the account owner can change this." in body
    c.post("/app/security/details/", {"full_name": "Bola Member", "business_name": "Hijacked Ltd"})
    organization.refresh_from_db()
    member.refresh_from_db()
    assert organization.name != "Hijacked Ltd" and member.full_name == "Bola Member"
    from apps.organizations.models import Membership

    with pytest.raises(PermissionFailure):
        profile.update_details(member, full_name="Bola Member",
                               membership=Membership.objects.get(user=member), business_name="Hijacked Ltd")


def test_a_name_is_required(owner):
    with pytest.raises(ValidationFailure):
        profile.update_details(owner, full_name=" ")


def test_nothing_changed_writes_no_audit_event(client, owner):
    client.post("/app/security/details/", {"full_name": owner.full_name, "business_name": owner.memberships.first().organization.name})
    assert not AuditEvent.objects.filter(action="account.details_changed").exists()


# ---------- contact preferences ----------

def test_marketing_is_off_by_default_and_each_change_is_a_new_record(client, owner):
    assert owner.updates_by_email and not owner.marketing_by_email
    client.post("/app/security/preferences/", {"updates_by_email": "on", "marketing_by_email": "on"})
    client.post("/app/security/preferences/", {"updates_by_email": "on", "marketing_by_email": "on"})  # no change
    client.post("/app/security/preferences/", {"updates_by_email": "on"})
    records = list(ConsentRecord.objects.filter(user=owner, kind=ConsentKind.MARKETING).order_by("created_at"))
    assert [r.granted for r in records] == [True, False]
    assert all(r.channel == "email" and "turn this off" in r.wording for r in records)
    assert AuditEvent.objects.filter(action="account.consent_withdrawn", actor=owner).count() == 1


def test_turning_off_email_updates_is_recorded(client, owner):
    client.post("/app/security/preferences/", {})
    owner.refresh_from_db()
    assert not owner.updates_by_email
    assert AuditEvent.objects.get(action="account.preferences_changed").metadata == {"updates_by_email": "off"}


def test_staff_have_no_client_preferences(staff_client):
    assert staff_client.post("/app/security/preferences/", {"marketing_by_email": "on"}).status_code == 404


def test_the_security_page_shows_the_preferences(client):
    body = client.get("/app/security/").content.decode()
    assert "How we contact you" in body and 'name="marketing_by_email"' in body
    assert 'name="marketing_by_email" class="sw" checked' not in body  # off by default


# ---------- request updates by email ----------

def _emails():
    return [e for e in OutboxEvent.objects.filter(topic=OutboxEvent.Topic.EMAIL)]


def test_request_updates_are_emailed_without_their_content(owner, organization, ops_user):
    User.objects.filter(pk=owner.pk).update(email_verified_at="2026-11-01T00:00Z")
    owner.refresh_from_db()
    communications.notify([owner, ops_user], organization_id=organization.pk,
                          kind=NotificationKind.TASK_MESSAGE, title="New message on BAE-1000")
    (event,) = _emails()  # the client only, not staff
    assert event.payload["to"] == [owner.email]
    assert "BAE-1000" in event.payload["subject"] and "/notifications/" in event.payload["body"]


def test_no_update_emails_when_turned_off_or_for_billing(owner, organization):
    User.objects.filter(pk=owner.pk).update(email_verified_at="2026-11-01T00:00Z", updates_by_email=False)
    owner.refresh_from_db()
    communications.notify([owner], organization_id=organization.pk, kind=NotificationKind.SUPPORT, title="Reply on SUP-1")
    User.objects.filter(pk=owner.pk).update(updates_by_email=True)
    owner.refresh_from_db()
    communications.notify([owner], organization_id=organization.pk, kind=NotificationKind.BILLING, title="Invoice due")
    assert _emails() == []
