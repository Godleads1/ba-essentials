"""The "Need Counselling?" button: clients only, set by the super admin, never tracked."""
from __future__ import annotations

import json
import re

import pytest
from django.core.exceptions import ValidationError

from apps.audit.models import AuditEvent
from apps.communications.models import CounsellingSettings

from .conftest import login_client, make_user


def _config(body: str):
    m = re.search(r'<script id="bandaCounselConfig" type="application/json">(.*?)</script>', body, re.DOTALL)
    return json.loads(m.group(1)) if m else None


def test_clients_see_the_button_on_old_and_new_pages(client, owner):
    for path in ("/app/", "/app/security/"):
        body = client.get(path).content.decode()
        cfg = _config(body)
        assert cfg and cfg["label"] == "Need Counselling?", path
        assert cfg["email"] == "counselingunit@bandaessentials.com"
        assert cfg["prayerLink"] == "" and cfg["phone"] == ""
        assert "banda/counsel.js" in body


def test_staff_do_not_see_it(ops_user):
    body = login_client(ops_user).get("/staff/").content.decode()
    assert _config(body) is None and "counsel.js" not in body


def test_it_can_be_switched_off_and_changed(client, owner):
    s = CounsellingSettings.current()
    s.prayer_link = "https://meet.example.com/banda-prayer"
    s.prayer_when = "Every Monday, 7:00–8:00am (WAT)"
    s.counselling_phone = "+234 800 000 0000"
    s.save()
    cfg = _config(client.get("/app/").content.decode())
    assert cfg["prayerLink"].startswith("https://") and cfg["phone"] == "+234 800 000 0000"
    s.enabled = False
    s.save()
    assert _config(client.get("/app/").content.decode()) is None


def test_values_are_escaped_safely(client, owner):
    s = CounsellingSettings.current()
    s.prayer_when = "</script><script>alert(1)</script>"
    s.save()
    body = client.get("/app/").content.decode()
    assert "<script>alert(1)" not in body
    assert _config(body)["prayerWhen"] == "</script><script>alert(1)</script>"


def test_the_call_link_must_be_https(db):
    s = CounsellingSettings.current()
    for bad in ("http://meet.example.com/x", "javascript:alert(1)"):
        s.prayer_link = bad
        with pytest.raises(ValidationError):
            s.full_clean()


def test_only_super_admins_can_change_it_and_changes_are_audited(db):
    from django.test import Client

    boss = make_user("boss@example.test", is_staff=True, is_superuser=True, mfa=True)
    ops = make_user("ops-admin@example.test", is_staff=True, mfa=True)
    CounsellingSettings.current()
    c = Client()
    c.force_login(ops)
    assert c.get("/admin/communications/counsellingsettings/1/change/").status_code in (302, 403)
    c = login_client(boss)
    r = c.post("/admin/communications/counsellingsettings/1/change/", {
        "enabled": "on", "button_label": "Need Counselling?", "counselling_email": "counselingunit@bandaessentials.com",
        "counselling_phone": "", "prayer_link": "https://meet.example.com/p", "prayer_when": "", "emergency_number": "112",
    })
    assert r.status_code in (200, 302)
    assert CounsellingSettings.current().prayer_link == "https://meet.example.com/p"
    event = AuditEvent.objects.get(action="communications.counselling_settings_changed")
    assert event.metadata["prayer_link"] == " → https://meet.example.com/p"


def test_opening_it_is_not_tracked():
    from pathlib import Path

    js = Path("static/banda/counsel.js").read_text()
    assert "fetch(" not in js and "XMLHttpRequest" not in js and "sendBeacon" not in js and "localStorage" not in js


def test_billing_settings_changes_are_recorded_too(db):
    from apps.billing.models import BillingSettings

    boss = make_user("boss2@example.test", is_staff=True, is_superuser=True, mfa=True)
    BillingSettings.current()
    login_client(boss).post("/admin/billing/billingsettings/1/change/", {
        "yearly_discount_pct": "20", "extra_legal_fee_ngn_minor": "2500000", "extra_legal_fee_usd_minor": "4000",
        "africa_surcharge_ngn_minor": "10000", "fallback_ngn_per_usd": "",
    })
    event = AuditEvent.objects.get(action="billing.settings_changed")
    assert event.metadata["yearly_discount_pct"] == "15 → 20"
