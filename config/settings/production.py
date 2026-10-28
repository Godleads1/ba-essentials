from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from config.env import env, env_bool, env_int

from .base import *  # noqa: F401,F403

DEBUG = False

_reachable_bugs: list[str] = []


def _require(condition: bool, message: str) -> None:
    if not condition:
        _reachable_bugs.append(message)


_secret = env("DJANGO_SECRET_KEY", "")
_require(bool(_secret), "DJANGO_SECRET_KEY must be set")
_require(len(_secret) >= 50, "DJANGO_SECRET_KEY must be at least 50 characters")
_require("insecure" not in _secret, "DJANGO_SECRET_KEY must not be a development key")

_allowed = [host for host in ALLOWED_HOSTS if host]
_require(bool(_allowed), "DJANGO_ALLOWED_HOSTS must list explicit hosts")
_require("*" not in _allowed, "DJANGO_ALLOWED_HOSTS must not use a wildcard")
_require(
    APP_BASE_URL.startswith("https://"),
    "APP_BASE_URL must use https in production",
)
_require(
    not DATABASES["default"]["ENGINE"].endswith("sqlite3"),
    "DATABASE_URL must point at PostgreSQL (or another server database) in production",
)
_require(SECURE_SSL_REDIRECT, "SECURE_SSL_REDIRECT must be enabled")
_require(SESSION_COOKIE_SECURE, "SECURE_COOKIE must be enabled")
_require(CSRF_COOKIE_SECURE, "SECURE_COOKIE must be enabled")
_require(
    SECURE_HSTS_SECONDS >= 31_536_000,
    "SECURE_HSTS_SECONDS must be at least one year",
)
_require(
    EMAIL_BACKEND != "django.core.mail.backends.console.EmailBackend",
    "A real transactional email backend is required in production",
)
_require(
    FILE_SCAN_BACKEND != "noop",
    "FILE_SCAN_BACKEND must name a real malware scanner in production",
)
_require(
    "LocMemCache" not in CACHES["default"]["BACKEND"],
    "A shared cache (for example Redis) is required for rate limiting across processes",
)

if PAYMENTS_ENABLED:
    _require(
        bool(PAYSTACK_SECRET_KEY),
        "PAYSTACK_SECRET_KEY is required while payments are enabled",
    )
    _require(
        bool(PAYSTACK_WEBHOOK_SECRET),
        "PAYSTACK_WEBHOOK_SECRET is required while payments are enabled",
    )

if _reachable_bugs:
    raise ImproperlyConfigured(
        "Refusing to start with an unsafe production configuration:\n - "
        + "\n - ".join(_reachable_bugs)
    )

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 31_536_000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": STATICFILES_STORAGE},
}
SECURE_REFERRER_POLICY = "same-origin"
