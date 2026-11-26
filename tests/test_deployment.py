"""The Hostinger server setup (deploy/, docs/HOSTINGER.md) keeps its security promises.

The stack itself was started and exercised by hand (see the pull request); these tests stop
the files drifting: what is reachable, which database account the app uses, and that no
secret is ever written into the repository.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _compose() -> dict:
    return yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))


def _services() -> dict:
    return _compose()["services"]


def test_only_caddy_faces_the_internet():
    services = _services()
    assert services["caddy"]["network_mode"] == "host"  # so ufw (Cloudflare only) governs 80/443
    assert services["web"]["ports"] == ["127.0.0.1:8000:8000"]
    for name in ("db", "redis", "clamav", "migrate"):
        assert "ports" not in services[name], name
        assert services[name].get("network_mode") != "host", name


def test_the_app_uses_the_limited_database_account_and_migrations_the_owner():
    services = _services()
    assert services["web"]["environment"]["DATABASE_URL"].startswith("postgres://banda_app:")
    assert services["migrate"]["environment"]["DATABASE_URL"].startswith("postgres://banda_owner:")
    assert services["migrate"]["profiles"] == ["tools"]  # never runs on its own
    init = (DEPLOY / "postgres-init" / "01-banda-roles.sh").read_text(encoding="utf-8")
    assert "CREATE DATABASE banda OWNER banda_owner;" in init
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO banda_app;" in init
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC;" in init
    assert not re.search(r"GRANT\s+(ALL|CREATE)[^;]*TO banda_app", init)


def test_the_app_container_is_locked_down():
    web = _services()["web"]
    assert web["user"] == "10001:10001" and web["read_only"] is True
    assert web["cap_drop"] == ["ALL"] and "cap_add" not in web
    assert "no-new-privileges:true" in web["security_opt"]
    env = web["environment"]
    assert env["DJANGO_SETTINGS_MODULE"] == "config.settings.production"
    assert env["CLIENT_IP_HEADER"] == "HTTP_CF_CONNECTING_IP"
    assert env["FILE_SCAN_BACKEND"] == "clamdscan" and env["CLAMD_CONFIG_FILE"] == "/tmp/clamd.conf"
    assert env["CACHE_BACKEND"].endswith("RedisCache")


def test_no_secret_is_written_into_the_repository():
    text = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    for name in ("POSTGRES_PASSWORD", "BANDA_OWNER_PASSWORD", "BANDA_APP_PASSWORD", "REDIS_PASSWORD"):
        assert f"${{{name}:?}}" in text, name  # required from deploy/.env, never a default
    example = (DEPLOY / "env.example").read_text(encoding="utf-8")
    secrets = ("DJANGO_SECRET_KEY", "MFA_ENCRYPTION_KEY", "POSTGRES_PASSWORD", "BANDA_OWNER_PASSWORD",
               "BANDA_APP_PASSWORD", "REDIS_PASSWORD", "EMAIL_HOST_PASSWORD", "R2_SECRET_ACCESS_KEY",
               "BACKUP_R2_SECRET_ACCESS_KEY", "BACKUP_ENCRYPTION_KEY", "PAYSTACK_SECRET_KEY",
               "FLUTTERWAVE_SECRET_KEY", "TERMII_API_KEY")
    for name in secrets:
        assert re.search(rf"^{name}=$", example, re.MULTILINE), name
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignored.splitlines() and "deploy/certs/" in ignored


def test_the_database_version_matches_ci_and_the_backup_tools():
    image = _services()["db"]["image"]
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    ci_major = re.search(r"image: postgres:(\d+)", ci).group(1)
    assert image.startswith(f"postgres:{ci_major}-")
    # pg_dump in the app image (Debian's postgresql-client) must not be newer than the server,
    # or its dumps can't be restored (found when testing this setup: PostgreSQL 17 vs 16).
    assert "postgresql-client" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_the_image_has_the_virus_scanner_client():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^\s+clamdscan \\$", dockerfile, re.MULTILINE)  # the "clamav" package doesn't include it
    assert "command -v clamdscan" in (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_every_scheduled_job_exists():
    cron = (DEPLOY / "banda.cron").read_text(encoding="utf-8")
    jobs = re.findall(r"\$RUN (\w+)", cron)
    assert set(jobs) == {"process_outbox", "scan_files", "run_renewals", "run_backups", "run_retention",
                         "restore_check", "send_monthly_summaries"}
    commands = {p.stem for p in ROOT.glob("apps/*/management/commands/*.py")}
    assert set(jobs) <= commands
    for line in cron.splitlines():
        if "$RUN" in line:
            assert line.split()[5] == "deploy", line  # never as root


def test_caddy_uses_the_cloudflare_origin_certificate_and_the_local_app():
    caddyfile = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "tls /certs/origin.pem /certs/origin.key" in caddyfile
    assert "reverse_proxy 127.0.0.1:8000" in caddyfile
    assert "admin off" in caddyfile


def test_ssh_settings_sort_before_cloud_init():
    harden = (DEPLOY / "harden.sh").read_text(encoding="utf-8")
    # sshd keeps the FIRST value it reads, so our file must sort before 50-cloud-init.conf.
    assert "/etc/ssh/sshd_config.d/01-banda.conf" in harden
    for setting in ("PermitRootLogin no", "PasswordAuthentication no", "KbdInteractiveAuthentication no"):
        assert setting in harden
    assert "sshd -t" in harden
