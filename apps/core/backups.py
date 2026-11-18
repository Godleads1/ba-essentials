"""Backups (audit finding F3; Privacy Policy section 5: "rotated within two days; deletions are
reapplied if a backup is restored").

- Database: `pg_dump` (custom format), encrypted on this server with BACKUP_ENCRYPTION_KEY
  (apps/core/backup_crypto.py), then stored as `db/<time>.dump.enc` with a small
  `db/<time>.json` of row counts used by the restore check. Copies older than
  BACKUP_KEEP_HOURS (48) are deleted after each successful backup.
- Files: every private file the database knows about is copied to `files/<key>`. A copy
  whose file has been deleted (by retention or closure) is removed within
  BACKUP_KEEP_HOURS of being noticed, so deletions reach the backups within two days.
- Restore check: the newest database backup is decrypted, read by `pg_restore`, and,
  when RESTORE_CHECK_DATABASE_URL names a scratch database, restored into it and its row
  counts compared with the counts taken at backup time.

Where: BACKUP_BACKEND=r2 (a separate private R2 bucket, BACKUP_R2_BUCKET) or local
(BACKUP_DIR, for development). Database passwords go to pg_dump through the
environment, never on the command line.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import dj_database_url
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

from . import backup_crypto

STAMP = "%Y%m%dT%H%M%SZ"


class BackupError(Exception):
    pass


# ---------- where backups live ----------

class LocalTarget:
    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.BACKUP_DIR)

    def _path(self, key: str) -> Path:
        if ".." in key or key.startswith("/"):
            raise BackupError("Invalid backup key.")
        return self.root / key

    def put(self, local: Path, key: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)
        os.chmod(target, 0o600)

    def put_stream(self, stream, key: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out:
            shutil.copyfileobj(stream, out)
        os.chmod(target, 0o600)

    def get(self, key: str, local: Path) -> None:
        shutil.copyfile(self._path(key), local)

    def read_text(self, key: str) -> str:
        return self._path(key).read_text()

    def list(self, prefix: str) -> list[tuple[str, datetime]]:
        base = self._path(prefix)
        if not base.exists():
            return []
        return sorted(
            (str(p.relative_to(self.root)), datetime.fromtimestamp(p.stat().st_mtime, tz=UTC))
            for p in base.rglob("*") if p.is_file()
        )

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class R2Target:
    def __init__(self, client=None, bucket: str | None = None):
        self.bucket = bucket or settings.BACKUP_R2_BUCKET
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "s3", endpoint_url=settings.R2_ENDPOINT_URL, region_name="auto",
                aws_access_key_id=settings.BACKUP_R2_ACCESS_KEY_ID or settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.BACKUP_R2_SECRET_ACCESS_KEY or settings.R2_SECRET_ACCESS_KEY,
            )
        return self._client

    def put(self, local: Path, key: str) -> None:
        self.client.upload_file(str(local), self.bucket, key)

    def put_stream(self, stream, key: str) -> None:
        self.client.upload_fileobj(stream, self.bucket, key)

    def get(self, key: str, local: Path) -> None:
        self.client.download_file(self.bucket, key, str(local))

    def read_text(self, key: str) -> str:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode()

    def list(self, prefix: str) -> list[tuple[str, datetime]]:
        found = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            found += [(o["Key"], o["LastModified"]) for o in page.get("Contents", [])]
        return sorted(found)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def get_target():
    backend = settings.BACKUP_BACKEND
    if backend == "local":
        return LocalTarget()
    if backend == "r2":
        if not settings.BACKUP_R2_BUCKET or settings.BACKUP_R2_BUCKET == settings.R2_BUCKET:
            raise ImproperlyConfigured("BACKUP_R2_BUCKET must be set, and must not be the files bucket.")
        return R2Target()
    raise ImproperlyConfigured(f"BACKUP_BACKEND={backend!r} is not supported. Use 'local' or 'r2'.")


# ---------- database ----------

def _pg_env(db: dict) -> dict:
    if "postgresql" not in db.get("ENGINE", ""):
        raise BackupError("Database backups need PostgreSQL.")
    env = {**os.environ, "PGHOST": str(db.get("HOST") or ""), "PGPORT": str(db.get("PORT") or "5432"),
           "PGUSER": str(db.get("USER") or ""), "PGPASSWORD": str(db.get("PASSWORD") or ""),
           "PGDATABASE": str(db.get("NAME") or "")}
    sslmode = (db.get("OPTIONS") or {}).get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = sslmode
    return env


def _run(command: list[str], env: dict) -> None:
    result = subprocess.run(command, env=env, capture_output=True, timeout=3600, check=False)
    if result.returncode != 0:
        # stderr can name hosts and users, never the password (it's only in the environment).
        raise BackupError(f"{command[0]} failed: {result.stderr.decode('utf-8', 'replace')[-300:]}")


def _row_counts() -> dict:
    counts = {}
    with connection.cursor() as cursor:
        for table in sorted(connection.introspection.table_names()):
            cursor.execute(f'SELECT count(*) FROM "{table}"')  # names come from the catalogue, not users
            counts[table] = cursor.fetchone()[0]
    return counts


def _rotate(target, prefix: str, now: datetime, keep: set[str]) -> int:
    cutoff = now - timedelta(hours=settings.BACKUP_KEEP_HOURS)
    removed = 0
    for key, modified in target.list(prefix):
        if key not in keep and modified < cutoff:
            target.delete(key)
            removed += 1
    return removed


def backup_database(now: datetime | None = None, target=None) -> dict:
    now = now or datetime.now(tz=UTC)
    target = target or get_target()
    backup_crypto._key()  # fail before dumping if the key is missing or wrong
    stamp = now.strftime(STAMP)
    dump_key, meta_key = f"db/{stamp}.dump.enc", f"db/{stamp}.json"
    with tempfile.TemporaryDirectory(prefix="banda-backup-") as work:
        dump, sealed = Path(work, "db.dump"), Path(work, "db.dump.enc")
        _run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", f"--file={dump}"],
             _pg_env(settings.DATABASES["default"]))
        counts = _row_counts()
        with dump.open("rb") as source, sealed.open("wb") as destination:
            backup_crypto.encrypt_stream(source, destination)
        dump.unlink()
        target.put(sealed, dump_key)
        meta = Path(work, "meta.json")
        meta.write_text(json.dumps({"created": now.isoformat(), "tables": counts}))
        target.put(meta, meta_key)
        size = sealed.stat().st_size
    removed = _rotate(target, "db/", now, keep={dump_key, meta_key})
    return {"backup": dump_key, "bytes": size, "tables": len(counts), "rotated": removed}


# ---------- files ----------

MISSING = "files/_missing.json"


def backup_files(now: datetime | None = None, target=None) -> dict:
    from apps.files.models import PrivateFile
    from apps.files.storage import get_storage

    now = now or datetime.now(tz=UTC)
    target = target or get_target()
    source = get_storage()
    from apps.website.models import PageImage

    live = set(PrivateFile.objects.values_list("storage_key", flat=True))
    live |= set(PageImage.objects.values_list("storage_key", flat=True))  # website images too
    present = {key[len("files/"):] for key, _ in target.list("files/") if key != MISSING}
    copied = 0
    for key in sorted(live - present):
        if not source.exists(key):
            continue  # recorded but already gone from storage
        with source.open(key) as stream:
            target.put_stream(stream, f"files/{key}")
        copied += 1
    # Copies of deleted files: note when first seen missing, remove within the keep window.
    try:
        missing = json.loads(target.read_text(MISSING))
    except Exception:  # noqa: BLE001 - first run, or an unreadable list: start a fresh one
        missing = {}
    gone = present - live
    missing = {k: v for k, v in missing.items() if k in gone}
    # Run daily: a deletion is noticed within a day, and the copy is removed on the run a day
    # later (half the keep window, less an hour of slack for timer drift): gone within two days.
    # The day's wait means a mistaken deletion can still be recovered from the mirror.
    wait = timedelta(hours=settings.BACKUP_KEEP_HOURS / 2 - 1)
    removed = 0
    for key in sorted(gone):
        first_seen = datetime.fromisoformat(missing.setdefault(key, now.isoformat()))
        if now - first_seen >= wait:
            target.delete(f"files/{key}")
            missing.pop(key)
            removed += 1
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(missing, handle)
    try:
        target.put(Path(handle.name), MISSING)
    finally:
        os.unlink(handle.name)
    return {"copied": copied, "removed": removed, "waiting_to_remove": len(missing)}


# ---------- restore check ----------

def restore_check(target=None, restore_url: str | None = None) -> dict:
    target = target or get_target()
    dumps = [k for k, _ in target.list("db/") if k.endswith(".dump.enc")]
    if not dumps:
        raise BackupError("There is no database backup to check.")
    latest = dumps[-1]
    meta = json.loads(target.read_text(latest.replace(".dump.enc", ".json")))
    result = {"backup": latest, "created": meta["created"], "decrypted": False, "readable": False, "restored": False}
    with tempfile.TemporaryDirectory(prefix="banda-restore-") as work:
        sealed, dump = Path(work, "db.dump.enc"), Path(work, "db.dump")
        target.get(latest, sealed)
        with sealed.open("rb") as source, dump.open("wb") as destination:
            backup_crypto.decrypt_stream(source, destination)
        result["decrypted"] = True
        _run(["pg_restore", "--list", str(dump)], dict(os.environ))
        result["readable"] = True
        restore_url = restore_url if restore_url is not None else settings.RESTORE_CHECK_DATABASE_URL
        if restore_url:
            scratch = dj_database_url.parse(restore_url)
            live = settings.DATABASES["default"]
            if (scratch.get("HOST"), str(scratch.get("PORT") or 5432), scratch.get("NAME")) == (
                    live.get("HOST"), str(live.get("PORT") or 5432), live.get("NAME")):
                raise BackupError("RESTORE_CHECK_DATABASE_URL must be a scratch database, never the live one.")
            env = _pg_env(scratch)
            _run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
                  f"--dbname={scratch['NAME']}", str(dump)], env)
            restored = _count_in(env)
            expected = meta["tables"]
            differences = {t: (expected[t], restored.get(t)) for t in expected if restored.get(t) != expected[t]}
            if differences:
                raise BackupError(f"Restored row counts differ: {dict(list(differences.items())[:5])}")
            result.update(restored=True, tables=len(expected), rows=sum(expected.values()))
    return result


def _count_in(env: dict) -> dict:
    query = ("SELECT table_name FROM information_schema.tables "
             "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")
    out = subprocess.run(["psql", "-At", "-c", query], env=env, capture_output=True, timeout=600, check=True)
    counts = {}
    for table in out.stdout.decode().split():
        row = subprocess.run(["psql", "-At", "-c", f'SELECT count(*) FROM "{table}"'], env=env,
                             capture_output=True, timeout=600, check=True)
        counts[table] = int(row.stdout.decode().strip())
    return counts
