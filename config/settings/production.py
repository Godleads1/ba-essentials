from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from config.env import env, env_bool, env_int

from .base import *

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
    PRIVATE_STORAGE_BACKEND in ("local", "r2"),
    "PRIVATE_STORAGE_BACKEND must be 'local' or 'r2'",
)
if PRIVATE_STORAGE_BACKEND == "r2":
    _require(
        bool(R2_ENDPOINT_URL.startswith("https://") and R2_BUCKET and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY),
        "R2_ENDPOINT_URL (https), R2_BUCKET, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY are required for R2 storage",
    )
if MFA_ENCRYPTION_KEY:
    from cryptography.fernet import Fernet as _Fernet

    try:
        _Fernet(MFA_ENCRYPTION_KEY)
    except ValueError:
        _require(False, "MFA_ENCRYPTION_KEY must be a Fernet key (32 bytes, URL-safe base64)")
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
    _require(
        bool(FLUTTERWAVE_SECRET_KEY),
        "FLUTTERWAVE_SECRET_KEY is required while payments are enabled",
    )
    _require(
        bool(FLUTTERWAVE_WEBHOOK_HASH),
        "FLUTTERWAVE_WEBHOOK_HASH is required while payments are enabled",
    )

if _reachable_bugs:
    raise ImproperlyConfigured(
        "Refusing to start with an unsafe production configuration:\n - "
        + "\n - ".join(_reachable_bugs)
    )

SECURE_SSL_REDIRECT = True
# Platform health checks reach the container over plain HTTP on the private
# network. This endpoint returns only a status, never tenant data.
SECURE_REDIRECT_EXEMPT = [r"^/health/$"]
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
