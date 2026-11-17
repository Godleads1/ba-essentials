"""Security alerts (audit finding F7): a person hears about lockouts, staff access changes and probing."""
from __future__ import annotations

import pytest
from django.core.cache import cache
from django.core.checks import run_checks
from django.test import Client, override_settings

from apps.audit import alerts
from apps.audit import services as audit
from apps.core.models import OutboxEvent

from .conftest import PASSWORD, login_client, make_user

ALERTS_TO = ["security@example.test"]


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


def _alerts():
    return [e for e in OutboxEvent.objects.filter(topic=OutboxEvent.Topic.EMAIL)
            if e.payload["subject"].startswith("[banda security]")]


@override_settings(SECURITY_ALERT_EMAILS=ALERTS_TO)
def test_staff_lockout_alerts_once(db, settings):
    staff = make_user("locked.staff@example.test", staff_role="operations")
    for _ in range(settings.LOGIN_MAX_FAILURES * 2 + 1):
        Client().post("/login/staff/", {"email": staff.email, "password": "wrong-password"})
    sent = [e for e in _alerts() if "locked" in e.payload["subject"]]
    assert len(sent) == 1  # throttled: one an hour for the same account
    assert sent[0].payload["to"] == ALERTS_TO and staff.email in sent[0].payload["body"]
    assert PASSWORD not in sent[0].payload["body"] and "wrong-password" not in sent[0].payload["body"]


@override_settings(SECURITY_ALERT_EMAILS=ALERTS_TO)
def test_client_lockout_does_not_alert(owner):
    audit.record(action="auth.lockout", actor=owner, resource_type="user", resource_id=str(owner.pk), result="denied")
    assert _alerts() == []


@override_settings(SECURITY_ALERT_EMAILS=ALERTS_TO)
def test_third_refused_download_in_an_hour_alerts(other_owner, organization, owner, submitted_task):
    from apps.files import services as files

    from .conftest import pdf_upload

    private = files.store_upload(organization=organization, user=owner, uploaded_file=pdf_upload())
    c = login_client(other_owner)
    for expected in (0, 0, 1, 1):
        c.get(f"/app/files/{private.pk}/download/")
        assert len(_alerts()) == expected
    assert "Repeated refused file downloads" in _alerts()[0].payload["subject"]
    assert other_owner.email in _alerts()[0].payload["body"]


@override_settings(SECURITY_ALERT_EMAILS=ALERTS_TO)
def test_granting_and_changing_staff_access_alerts(db):
    user = make_user("new.hire@example.test", staff_role="specialist")
    assert any(user.email in e.payload["body"] and "specialist" in e.payload["body"] for e in _alerts())
    cache.clear()
    user.is_superuser = True
    user.save()
    assert any("is_superuser: False -> True" in e.payload["body"] for e in _alerts())


@override_settings(SECURITY_ALERT_EMAILS=ALERTS_TO)
def test_new_clients_and_profile_edits_do_not_alert(db):
    client_user = make_user("plain.client@example.test")
    client_user.full_name = "Renamed"
    client_user.save()
    assert _alerts() == []


@override_settings(SECURITY_ALERT_EMAILS=ALERTS_TO)
def test_staff_turning_off_mfa_alerts(db):
    staff = make_user("mfa.off@example.test", staff_role="operations")
    cache.clear()
    OutboxEvent.objects.all().delete()
    audit.record(action="auth.mfa_disabled", actor=staff, resource_type="user", resource_id=str(staff.pk))
    assert len(_alerts()) == 1


@override_settings(SECURITY_ALERT_EMAILS=ALERTS_TO)
def test_badly_signed_payment_notifications_alert_on_the_fifth(db):
    body = b'{"event":"charge.success","data":{"reference":"x","amount":1,"currency":"NGN"}}'
    for i in range(6):
        Client().post("/webhooks/paystack/", body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE="00")
        assert len(_alerts()) == (1 if i >= 4 else 0)


@override_settings(SECURITY_ALERT_EMAILS=[])
def test_without_recipients_alerts_are_only_logged(db, caplog):
    staff = make_user("quiet@example.test", staff_role="operations")
    with caplog.at_level("WARNING"):
        audit.record(action="auth.mfa_disabled", actor=staff, resource_type="user", resource_id=str(staff.pk))
    assert _alerts() == [] and "security_alert" in caplog.text


def test_an_alert_failure_never_breaks_the_action(owner, monkeypatch):
    monkeypatch.setattr(alerts, "_check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    event = audit.record(action="auth.lockout", actor=owner, resource_type="user", resource_id=str(owner.pk))
    assert event.pk


@override_settings(SECURITY_ALERT_EMAILS=[])
def test_deploy_check_warns_without_recipients():
    assert "banda.W002" in [m.id for m in run_checks(include_deployment_checks=True)]
