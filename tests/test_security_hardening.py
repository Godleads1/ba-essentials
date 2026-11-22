"""Security checklist 9 (breached passwords, admin address), 18 (photo metadata) and 25 (audit trail)."""
from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, override_settings
from django.urls import clear_url_caches
from PIL import Image

from apps.accounts import breached
from apps.accounts import services as account_services
from apps.audit import services as audit_service
from apps.audit.models import AuditEvent
from apps.core.exceptions import ValidationFailure
from apps.files import services as files

from .conftest import PASSWORD, login_client, make_user

# ---------- 9: staff passwords that appeared in breaches are refused ----------

NEW = "Lantern-Orchard-Basalt-77"


def test_a_breached_password_is_refused(owner, fake_pwned):
    fake_pwned.breached.add(NEW)
    with pytest.raises(ValidationFailure, match="appeared in a data breach"):
        account_services.change_password(owner, current_password=PASSWORD, new_password=NEW)
    owner.refresh_from_db()
    assert owner.check_password(PASSWORD)


def test_only_five_characters_of_the_hash_leave_the_server(owner, fake_pwned):
    account_services.change_password(owner, current_password=PASSWORD, new_password=NEW)
    (prefix,) = fake_pwned.prefixes
    assert len(prefix) == 5 and NEW not in prefix and all(c in "0123456789ABCDEF" for c in prefix)


def test_padding_lines_do_not_count_as_a_breach(db, fake_pwned):
    assert breached.times_breached(NEW) == 0


def test_if_the_service_is_down_the_other_rules_still_apply(owner, fake_pwned, caplog):
    fake_pwned.down = True
    account_services.change_password(owner, current_password=PASSWORD, new_password=NEW)
    assert "breached_password_check_unavailable" in caplog.text
    with pytest.raises(ValidationFailure):
        account_services.change_password(owner, current_password=NEW, new_password="short")


def test_create_staff_refuses_a_breached_password(db, fake_pwned, monkeypatch):
    from django.core.management import CommandError, call_command

    fake_pwned.breached.add(NEW)
    monkeypatch.setenv("BANDA_STAFF_PASSWORD", NEW)
    from apps.accounts.management.commands import create_staff

    monkeypatch.setattr(create_staff.Command, "_password", lambda self, noinput: NEW)
    with pytest.raises(CommandError, match="data breach"):
        call_command("create_staff", "new.staff@example.test", "--role", "operations", "--noinput")


# ---------- 9: Django admin can move off /admin/ ----------

@pytest.fixture
def admin_url(settings):
    def use(path):
        settings.ADMIN_URL = path
        import importlib

        import config.urls

        importlib.reload(config.urls)
        clear_url_caches()

    yield use
    settings.ADMIN_URL = "admin/"
    import importlib

    import config.urls

    importlib.reload(config.urls)
    clear_url_caches()


def test_admin_can_live_at_another_address(db, admin_url):
    admin_url("ops-desk/")
    superuser = make_user("super@example.test", staff_role="admin", mfa=True, is_staff=True, is_superuser=True)
    c = login_client(superuser)
    assert c.get("/ops-desk/").status_code == 200
    assert c.get("/admin/").status_code == 404
    r = Client().get("/ops-desk/login/?next=/ops-desk/accounts/user/")
    assert r.status_code == 302 and "next=%2Fops-desk%2Faccounts%2Fuser%2F" in r["Location"]
    r = Client().get("/ops-desk/login/?next=https://evil.example/")
    assert "next=%2Fops-desk%2F" in r["Location"]


def test_the_admin_address_must_be_plain(monkeypatch):
    import importlib

    from django.core.exceptions import ImproperlyConfigured

    from config.settings import base

    monkeypatch.setenv("ADMIN_URL", "../x?y")
    with pytest.raises(ImproperlyConfigured):
        importlib.reload(base)
    monkeypatch.delenv("ADMIN_URL")
    importlib.reload(base)


# ---------- 18: photos lose their metadata ----------

GPS = 0x8825


def _photo(fmt: str, *, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (64, 32), (200, 80, 40))
    exif = Image.Exif()
    exif[0x010F] = "SyntheticCam"  # camera make
    exif[GPS] = {1: "N", 2: (6.0, 30.0, 0.0)}  # a GPS position
    if orientation:
        exif[0x0112] = orientation
    out = io.BytesIO()
    if fmt == "JPEG":
        image.save(out, format="JPEG", quality=90, exif=exif.tobytes(), comment=b"<script>x</script>")
    else:
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        info.add_text("Comment", "<script>x</script>")
        image.save(out, format="PNG", pnginfo=info, exif=exif.tobytes())
    return out.getvalue()


def _stored(organization, owner, name, body):
    upload = SimpleUploadedFile(name, body, content_type="application/octet-stream")
    private = files.store_upload(organization=organization, user=owner, uploaded_file=upload)
    with files.get_storage().open(private.storage_key) as handle:
        return private, handle.read()


@pytest.mark.parametrize(("name", "fmt"), [("site.jpg", "JPEG"), ("site.jpeg", "JPEG"), ("logo.png", "PNG")])
def test_uploaded_photos_are_stored_without_metadata(organization, owner, name, fmt):
    import hashlib

    original = _photo(fmt)
    assert Image.open(io.BytesIO(original)).getexif().get(GPS)
    private, stored = _stored(organization, owner, name, original)
    image = Image.open(io.BytesIO(stored))
    assert image.format == fmt and image.size == (64, 32)
    assert not image.getexif() and b"SyntheticCam" not in stored and b"<script>" not in stored
    assert private.sha256 == hashlib.sha256(stored).hexdigest() and private.size_bytes == len(stored)


def test_a_rotated_photo_stays_the_right_way_up(organization, owner):
    _, stored = _stored(organization, owner, "portrait.jpg", _photo("JPEG", orientation=6))
    image = Image.open(io.BytesIO(stored))
    assert image.size == (32, 64) and not image.getexif()


def test_a_jpeg_keeps_its_quality(organization, owner):
    original = _photo("JPEG")
    _, stored = _stored(organization, owner, "site.jpg", original)
    before = Image.open(io.BytesIO(original)).quantization
    assert Image.open(io.BytesIO(stored)).quantization == before


def test_a_fake_image_is_refused(organization, owner):
    with pytest.raises(ValidationFailure, match="couldn't be read"):
        _stored(organization, owner, "fake.png", b"\x89PNG\r\n\x1a\n" + b"not really an image" * 10)


def test_other_files_are_stored_as_they_came(organization, owner):
    from .conftest import pdf_upload

    body = b"%PDF-1.4\n% synthetic\n"
    private = files.store_upload(organization=organization, user=owner, uploaded_file=pdf_upload(body=body))
    with files.get_storage().open(private.storage_key) as handle:
        assert handle.read() == body


# ---------- 25: the audit trail ----------

def _change_preferences(owner, **headers):
    login_client(owner).post("/app/security/preferences/", {}, **headers)  # turns updates off
    return AuditEvent.objects.get(action="account.preferences_changed")


def test_ip_addresses_are_not_kept_until_switched_on(owner):
    assert _change_preferences(owner, REMOTE_ADDR="198.51.100.7").ip_address is None


@override_settings(AUDIT_STORE_IP=True, CLIENT_IP_HEADER="HTTP_CF_CONNECTING_IP")
def test_when_switched_on_events_keep_the_visitor_address(owner):
    event = _change_preferences(owner, HTTP_CF_CONNECTING_IP="203.0.113.9", REMOTE_ADDR="10.0.0.2")
    assert event.ip_address == "203.0.113.9"  # Cloudflare's header, not the proxy's address
    outside = audit_service.record(action="outside.request", actor=owner)  # no request, no address
    assert outside.ip_address is None


@override_settings(AUDIT_STORE_IP=True, AUDIT_IP_RETENTION_DAYS=90)
def test_addresses_are_cleared_after_the_retention_period(db, ops_user):
    from datetime import timedelta
    from unittest import mock

    from django.utils import timezone

    from apps.core import context
    from apps.core.retention import run

    token = context.set_client_ip("203.0.113.9")
    try:
        with mock.patch("django.utils.timezone.now", return_value=timezone.now() - timedelta(days=91)):
            old = audit_service.record(action="old.event", actor=ops_user)
        new = audit_service.record(action="new.event", actor=ops_user)
    finally:
        context.reset_client_ip(token)
    assert run()["audit addresses cleared"] == 1
    old.refresh_from_db()
    new.refresh_from_db()
    assert old.ip_address is None and old.action == "old.event" and new.ip_address == "203.0.113.9"


def test_audit_events_cannot_be_edited_through_the_app(ops_user):
    event = audit_service.record(action="auth.login", actor=ops_user)
    event.action = "something.else"
    with pytest.raises(PermissionError):
        event.save()


@pytest.mark.skipif(connection.vendor != "postgresql", reason="the trigger exists on PostgreSQL")
def test_the_database_refuses_edits_to_audit_events(ops_user, organization):
    from django.db import DatabaseError, transaction

    event = audit_service.record(action="auth.login", actor=ops_user, organization=organization,
                                 metadata={"x": 1})
    for change in ({"action": "auth.nothing"}, {"result": "denied"}, {"metadata": {"x": 2}},
                   {"ip_address": "192.0.2.1"}, {"actor": make_user("other.actor@example.test")}):
        with pytest.raises(DatabaseError), transaction.atomic():
            AuditEvent.objects.filter(pk=event.pk).update(**change)
    # What Django and retention do is still allowed.
    AuditEvent.objects.filter(pk=event.pk).update(ip_address=None, actor=None, organization=None)
    AuditEvent.objects.filter(pk=event.pk).delete()
