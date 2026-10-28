from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.accounts.models import StaffRole, User
from apps.catalogue.models import ServiceDefinition, ServiceVersion
from apps.core.models import OutboxEvent
from apps.organizations.models import Organization

from .conftest import PASSWORD


def test_create_staff_creates_verified_staff_account(db, monkeypatch, capsys):
    monkeypatch.setenv("BA_STAFF_PASSWORD", PASSWORD)

    call_command(
        "create_staff",
        "new.ops@example.test",
        "--role",
        StaffRole.OPERATIONS,
        "--noinput",
    )

    user = User.objects.get(email="new.ops@example.test")
    assert user.staff_role == StaffRole.OPERATIONS
    assert user.email_verified_at is not None
    assert user.check_password(PASSWORD)
    assert PASSWORD not in capsys.readouterr().out


def test_create_staff_requires_a_strong_password(db, monkeypatch):
    monkeypatch.setenv("BA_STAFF_PASSWORD", "short")

    with pytest.raises(CommandError):
        call_command(
            "create_staff",
            "weak@example.test",
            "--role",
            StaffRole.SUPPORT,
            "--noinput",
        )


def test_bootstrap_catalogue_is_idempotent_and_versions_changes(db, tmp_path):
    manifest = tmp_path / "catalogue.json"
    manifest.write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Brand",
                        "slug": "brand",
                        "services": [
                            {
                                "name": "Logo",
                                "slug": "logo",
                                "scope_summary": "One logo direction.",
                                "deliverables_summary": "PNG and SVG.",
                                "state": "available",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    call_command("bootstrap_catalogue", str(manifest))
    definition = ServiceDefinition.objects.get(slug="logo")
    assert definition.versions.count() == 1

    call_command("bootstrap_catalogue", str(manifest))
    assert definition.versions.count() == 1

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["categories"][0]["services"][0]["scope_summary"] = "Two logo directions."
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    call_command("bootstrap_catalogue", str(manifest))

    assert definition.versions.count() == 2
    assert definition.versions.filter(is_current=True).count() == 1
    assert definition.versions.get(is_current=True).scope_summary == "Two logo directions."


@override_settings(DEBUG=True)
def test_seed_demo_creates_synthetic_data(db):
    call_command("seed_demo", "--yes")

    organization = Organization.objects.get(slug="demo-studio")
    assert organization.tasks.count() == 1
    assert User.objects.filter(email__endswith="@example.test").count() == 6
    assert ServiceVersion.objects.filter(is_current=True).exists()


def test_seed_demo_refuses_outside_debug():
    with pytest.raises(CommandError):
        call_command("seed_demo", "--yes")


def test_scan_files_refuses_the_noop_backend():
    with pytest.raises(CommandError):
        call_command("scan_files")


def test_process_outbox_delivers_and_marks_events(db, mailoutbox):
    event = OutboxEvent.objects.create(
        topic=OutboxEvent.Topic.EMAIL,
        payload={
            "to": ["synthetic@example.test"],
            "subject": "Synthetic event",
            "body": "Hello from a test.",
        },
    )

    call_command("process_outbox")

    event.refresh_from_db()
    assert event.processed_at is not None
    assert event.attempts == 1
    assert len(mailoutbox) == 1
