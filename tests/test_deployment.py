from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = ROOT / "render.yaml"


def _blueprint() -> dict:
    return yaml.safe_load(BLUEPRINT.read_text(encoding="utf-8"))


def _named(items: list[dict]) -> dict[str, dict]:
    return {item["name"]: item for item in items}


def _keys(items: list[dict]) -> dict[str, dict]:
    return {item["key"]: item for item in items if "key" in item}


def test_blueprint_provisions_database_cache_scanner_and_web():
    data = _blueprint()
    database = _named(data["databases"])["ba-essentials-staging-db"]
    services = _named(data["services"])

    assert database["postgresMajorVersion"] == "17"
    assert database["plan"] == "basic-256mb"

    cache = services["ba-essentials-staging-cache"]
    assert cache["type"] == "keyvalue"
    assert cache["ipAllowList"] == []

    scanner = services["ba-essentials-staging-clamav"]
    assert scanner["type"] == "pserv"
    assert scanner["runtime"] == "image"
    assert scanner["image"]["url"].startswith("docker.io/clamav/clamav:")

    web = services["ba-essentials-staging"]
    assert web["runtime"] == "docker"
    assert web["healthCheckPath"] == "/health/"
    assert web["preDeployCommand"] == "python manage.py migrate --noinput"
    assert web["disk"]["mountPath"] == "/var/data"


def test_web_and_cron_use_the_managed_database_and_cache():
    services = _named(_blueprint()["services"])
    web = services["ba-essentials-staging"]
    cron = services["ba-essentials-staging-outbox"]

    for service in (web, cron):
        env = _keys(service["envVars"])
        assert env["DATABASE_URL"]["fromDatabase"]["name"] == (
            "ba-essentials-staging-db"
        )
        assert env["CACHE_LOCATION"]["fromService"]["type"] == "keyvalue"

    web_env = _keys(web["envVars"])
    assert web_env["CLAMD_HOST"]["fromService"]["type"] == "pserv"

    assert cron["type"] == "cron"
    assert cron["schedule"] == "*/5 * * * *"
    assert cron["dockerCommand"] == "python manage.py process_outbox --limit 100"


def test_shared_environment_uses_redis_resend_and_clamdscan():
    group = _keys(_blueprint()["envVarGroups"][0]["envVars"])

    assert group["DJANGO_SETTINGS_MODULE"]["value"] == "config.settings.staging"
    assert group["CACHE_BACKEND"]["value"] == (
        "django.core.cache.backends.redis.RedisCache"
    )
    assert group["EMAIL_BACKEND"]["value"] == (
        "django.core.mail.backends.smtp.EmailBackend"
    )
    assert group["EMAIL_HOST"]["value"] == "smtp.resend.com"
    assert group["EMAIL_HOST_USER"]["value"] == "resend"
    assert group["FILE_SCAN_BACKEND"]["value"] == "clamdscan"


def test_blueprint_never_commits_secret_values():
    group = _keys(_blueprint()["envVarGroups"][0]["envVars"])

    for key in ("DJANGO_SECRET_KEY", "EMAIL_HOST_PASSWORD", "DEFAULT_FROM_EMAIL"):
        assert group[key].get("sync") is False
        assert "value" not in group[key]
