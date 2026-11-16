"""Production settings must fail closed when security inputs are unsafe.

These tests import the production settings in a clean subprocess so the
module-level checks run exactly as they would at deploy time.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]

SAFE_ENV = {
    "DJANGO_SECRET_KEY": "S" * 64,
    "DJANGO_ALLOWED_HOSTS": "app.example.test",
    "APP_BASE_URL": "https://app.example.test",
    "DATABASE_URL": "postgres://ba:pass@db.example.test:5432/ba",
    "SECURE_SSL_REDIRECT": "true",
    "SECURE_COOKIE": "true",
    "SECURE_HSTS_SECONDS": "31536000",
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "FILE_SCAN_BACKEND": "clamdscan",
    "CACHE_BACKEND": "django.core.cache.backends.redis.RedisCache",
    "CACHE_LOCATION": "redis://cache.example.test:6379/1",
    "PAYMENTS_ENABLED": "false",
}


def _clean_environment(overrides: dict[str, str | None]) -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env["PYTHONPATH"] = str(BASE_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update({key: value for key, value in SAFE_ENV.items() if value is not None})
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _import_production(overrides: dict[str, str | None]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", "import config.settings.production"],
        cwd=BASE_DIR,
        env=_clean_environment(overrides),
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_staging_adopts_the_render_hostname():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from config.settings import staging as s; "
            "print(s.APP_BASE_URL); "
            "print(','.join(s.ALLOWED_HOSTS)); "
            "print(','.join(s.CSRF_TRUSTED_ORIGINS)); "
            "print(','.join(s.SECURE_REDIRECT_EXEMPT))",
        ],
        cwd=BASE_DIR,
        env=_clean_environment(
            {"RENDER_EXTERNAL_HOSTNAME": "ba-essentials-staging.onrender.com"}
        ),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    app_base_url, allowed_hosts, trusted_origins, redirect_exempt = (
        result.stdout.strip().splitlines()
    )
    assert app_base_url == "https://ba-essentials-staging.onrender.com"
    assert "ba-essentials-staging.onrender.com" in allowed_hosts.split(",")
    assert "https://ba-essentials-staging.onrender.com" in trusted_origins.split(",")
    assert "^/health/$" in redirect_exempt.split(",")


def test_safe_production_configuration_imports():
    result = _import_production({})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"DJANGO_SECRET_KEY": None}, "DJANGO_SECRET_KEY must be set"),
        ({"DJANGO_SECRET_KEY": "short"}, "at least 50 characters"),
        (
            {"DJANGO_SECRET_KEY": "django-insecure-" + "x" * 60},
            "must not be a development key",
        ),
        ({"DJANGO_ALLOWED_HOSTS": None}, "must list explicit hosts"),
        ({"DJANGO_ALLOWED_HOSTS": "*"}, "must not use a wildcard"),
        ({"APP_BASE_URL": "http://app.example.test"}, "must use https"),
        (
            {"DATABASE_URL": "sqlite:///var/db.sqlite3"},
            "must point at PostgreSQL",
        ),
        ({"SECURE_SSL_REDIRECT": "false"}, "SECURE_SSL_REDIRECT must be enabled"),
        ({"SECURE_COOKIE": "false"}, "SECURE_COOKIE must be enabled"),
        ({"SECURE_HSTS_SECONDS": "0"}, "at least one year"),
        (
            {"EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"},
            "real transactional email backend",
        ),
        ({"FILE_SCAN_BACKEND": "noop"}, "real malware scanner"),
        (
            {"CACHE_BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "shared cache",
        ),
        (
            {"PAYMENTS_ENABLED": "true", "PAYSTACK_SECRET_KEY": None},
            "PAYSTACK_SECRET_KEY is required",
        ),
        (
            {
                "PAYMENTS_ENABLED": "true",
                "PAYSTACK_SECRET_KEY": "sk_test_x",
                "PAYSTACK_WEBHOOK_SECRET": None,
            },
            "PAYSTACK_WEBHOOK_SECRET is required",
        ),
    ],
)
def test_unsafe_production_configuration_refuses_to_start(overrides, expected):
    result = _import_production(overrides)
    assert result.returncode != 0
    assert "Refusing to start" in result.stderr
    assert expected in result.stderr
