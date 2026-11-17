"""Backups and the restore check (audit finding F3; Privacy Policy: rotated within two days)."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection

from apps.core import backup_crypto, backups

from .conftest import pdf_upload

KEY = backup_crypto.generate_key()


# ---------- encryption ----------

def _seal(data: bytes, key: str = KEY) -> bytes:
    out = io.BytesIO()
    backup_crypto.encrypt_stream(io.BytesIO(data), out, key)
    return out.getvalue()


def _open(sealed: bytes, key: str = KEY) -> bytes:
    out = io.BytesIO()
    backup_crypto.decrypt_stream(io.BytesIO(sealed), out, key)
    return out.getvalue()


@pytest.mark.parametrize("size", [0, 1, backup_crypto.CHUNK - 1, backup_crypto.CHUNK, backup_crypto.CHUNK * 2 + 5])
def test_round_trip(size):
    data = os.urandom(size)
    sealed = _seal(data)
    assert _open(sealed) == data and data[:64] not in sealed[16:] if size >= 64 else _open(sealed) == data


def _chunks(sealed: bytes):
    head, rest, parts = sealed[:16], sealed[16:], []
    while rest:
        n = int.from_bytes(rest[:4], "big")
        parts.append(rest[: 4 + n])
        rest = rest[4 + n:]
    return head, parts


def test_wrong_key_tampering_reordering_and_cutting_off_are_all_refused():
    data = os.urandom(backup_crypto.CHUNK * 3)
    sealed = _seal(data)
    head, parts = _chunks(sealed)
    flipped = bytearray(sealed)
    flipped[100] ^= 1
    cases = {
        "wrong key": (sealed, backup_crypto.generate_key()),
        "a changed byte": (bytes(flipped), KEY),
        "chunks swapped": (head + parts[1] + parts[0] + b"".join(parts[2:]), KEY),
        "last chunk cut off": (head + b"".join(parts[:-1]), KEY),
        "half a chunk": (sealed[:-10], KEY),
        "not a backup": (b"PGDMP" + data[:100], KEY),
        "only the header": (head, KEY),
    }
    for name, (blob, key) in cases.items():
        with pytest.raises(backup_crypto.BackupDecryptError):
            _open(blob, key)
        assert name


def test_a_bad_key_setting_is_refused(settings):
    settings.BACKUP_ENCRYPTION_KEY = "too-short"
    with pytest.raises(ImproperlyConfigured):
        backup_crypto.encrypt_stream(io.BytesIO(b"x"), io.BytesIO())


# ---------- files ----------

@pytest.fixture
def target(tmp_path, settings):
    settings.BACKUP_DIR = str(tmp_path / "backups")
    settings.BACKUP_BACKEND = "local"
    settings.BACKUP_ENCRYPTION_KEY = KEY
    return backups.LocalTarget()


def test_files_are_mirrored_and_deletions_reach_the_backup_within_two_days(target, organization, owner, settings, tmp_path):
    from apps.files import services as files
    from apps.files.models import PrivateFile

    settings.PRIVATE_STORAGE_ROOT = str(tmp_path / "private")
    kept = files.store_upload(organization=organization, user=owner, uploaded_file=pdf_upload("kept.pdf"))
    dropped = files.store_upload(organization=organization, user=owner, uploaded_file=pdf_upload("dropped.pdf"))
    day0 = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
    assert backups.backup_files(now=day0, target=target)["copied"] == 2
    assert backups.backup_files(now=day0 + timedelta(hours=1), target=target)["copied"] == 0  # nothing new

    PrivateFile.objects.filter(pk=dropped.pk).delete()  # retention or closure deleted it
    first = backups.backup_files(now=day0 + timedelta(days=1), target=target)
    assert first["removed"] == 0 and first["waiting_to_remove"] == 1  # kept a day, in case it was a mistake
    second = backups.backup_files(now=day0 + timedelta(days=2), target=target)
    assert second["removed"] == 1
    keys = {k for k, _ in target.list("files/")}
    assert f"files/{kept.storage_key}" in keys and f"files/{dropped.storage_key}" not in keys


def test_backups_bucket_must_differ_from_the_files_bucket(settings):
    settings.BACKUP_BACKEND, settings.R2_BUCKET, settings.BACKUP_R2_BUCKET = "r2", "banda-private", "banda-private"
    with pytest.raises(ImproperlyConfigured):
        backups.get_target()


def test_restore_check_refuses_the_live_database(target, settings):
    target.root.mkdir(parents=True)
    (target.root / "db").mkdir()
    (target.root / "db" / "20261120T020000Z.dump.enc").write_bytes(_seal(b"x"))
    (target.root / "db" / "20261120T020000Z.json").write_text(json.dumps({"created": "x", "tables": {}}))
    live = settings.DATABASES["default"]
    with pytest.raises(backups.BackupError):
        backups.restore_check(target=target, restore_url="postgres://u:p@{}:{}/{}".format(
            live.get("HOST") or "h", live.get("PORT") or 5432, live.get("NAME")))


def test_a_failed_backup_alerts_and_fails_the_job(target, settings, monkeypatch, db):
    from django.core.management.base import CommandError

    from apps.core.models import OutboxEvent

    settings.SECURITY_ALERT_EMAILS = ["security@example.test"]
    monkeypatch.setattr(backups, "backup_database", lambda: (_ for _ in ()).throw(backups.BackupError("disk full")))
    with pytest.raises(CommandError):
        call_command("run_backups", "--database-only")
    assert any("A backup failed" in e.payload.get("subject", "") for e in OutboxEvent.objects.all())


def test_new_key_prints_a_usable_key(capsys):
    call_command("run_backups", "--new-key")
    key = capsys.readouterr().out.splitlines()[0]
    assert _open(_seal(b"hello", key), key) == b"hello"


# ---------- database (PostgreSQL only) ----------

def _pg_tools_match_server() -> bool:
    if connection.vendor != "postgresql" or not shutil.which("pg_dump"):
        return False
    client = subprocess.run(["pg_dump", "--version"], capture_output=True, text=True, check=True).stdout
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version_num")
        server_major = int(cursor.fetchone()[0]) // 10000
    return int(client.split()[-1].split(".")[0]) >= server_major


@pytest.mark.django_db(transaction=True)
def test_database_backup_is_encrypted_rotated_and_restores(target, organization, owner, settings):
    if connection.vendor != "postgresql":
        pytest.skip("database backups need PostgreSQL")
    if not _pg_tools_match_server():
        pytest.skip("pg_dump older than the PostgreSQL server")
    now = datetime.now(tz=UTC)
    old = target.root / "db"
    old.mkdir(parents=True)
    for name in ("20200101T000000Z.dump.enc", "20200101T000000Z.json"):
        (old / name).write_bytes(b"old")
        stamp = (now - timedelta(hours=49)).timestamp()
        os.utime(old / name, (stamp, stamp))

    result = backups.backup_database(now=now, target=target)
    assert result["rotated"] == 2 and not (old / "20200101T000000Z.dump.enc").exists()
    sealed = (target.root / result["backup"]).read_bytes()
    assert sealed.startswith(b"BANDABK1") and b"PGDMP" not in sealed and owner.email.encode() not in sealed

    scratch = f"{settings.DATABASES['default']['NAME']}_restore"
    env = backups._pg_env(settings.DATABASES["default"])
    subprocess.run(["psql", "-c", f'DROP DATABASE IF EXISTS "{scratch}"'], env=env, check=True, capture_output=True)
    subprocess.run(["psql", "-c", f'CREATE DATABASE "{scratch}"'], env=env, check=True, capture_output=True)
    try:
        db = settings.DATABASES["default"]
        url = f"postgres://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db.get('PORT') or 5432}/{scratch}"
        checked = backups.restore_check(target=target, restore_url=url)
        assert checked["decrypted"] and checked["readable"] and checked["restored"] and checked["rows"] > 0
    finally:
        subprocess.run(["psql", "-c", f'DROP DATABASE IF EXISTS "{scratch}"'], env=env, capture_output=True, check=False)
