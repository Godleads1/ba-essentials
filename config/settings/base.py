from __future__ import annotations

import dj_database_url

from config.env import BASE_DIR, env, env_bool, env_int, env_list
from config.logging import build_logging

SECRET_KEY = env("DJANGO_SECRET_KEY", "django-insecure-local-development-only-key")
DEBUG = env_bool("DJANGO_DEBUG", False)
# No default hosts: every environment must name the hosts it serves. Local and
# test settings provide their own loopback values.
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
APP_BASE_URL = env("APP_BASE_URL", "http://localhost:8000")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.audit",
    "apps.accounts",
    "apps.organizations",
    "apps.catalogue",
    "apps.files",
    "apps.tasks",
    "apps.quotes",
    "apps.reviews",
    "apps.billing",
    "apps.operations",
]

MIDDLEWARE = [
    "apps.core.middleware.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "apps.core.middleware.SessionValidationMiddleware",
    "apps.accounts.middleware.StaffMFAEnforcementMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.navigation",
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", f"sqlite:///{BASE_DIR / 'var' / 'db.sqlite3'}"),
        conn_max_age=env_int("DATABASE_CONN_MAX_AGE", 60),
        conn_health_checks=True,
    )
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "var" / "static"
MEDIA_URL = "/media/"
# There is no public media root on purpose: customer files live in private
# storage and are only served through authorized, time-limited views.
MEDIA_ROOT = None

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Sessions and cookies -------------------------------------------------
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 8)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIE", False)
CSRF_COOKIE_SECURE = env_bool("SECURE_COOKIE", False)
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"
SESSION_IDLE_TIMEOUT = env_int("SESSION_IDLE_TIMEOUT", 60 * 60)
LOGIN_URL = "/login"
LOGIN_REDIRECT_URL = "/app/"
LOGOUT_REDIRECT_URL = "/login"

# --- Security headers and transport --------------------------------------
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

CONTENT_SECURITY_POLICY = env(
    "CONTENT_SECURITY_POLICY",
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'self'",
)

# --- Email ----------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "B & A Essentials <no-reply@example.com>")

# --- Cache / rate limiting ------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": env(
            "CACHE_BACKEND", "django.core.cache.backends.locmem.LocMemCache"
        ),
        "LOCATION": env("CACHE_LOCATION", "ba-essentials"),
    }
}

# --- Uploads and private storage -----------------------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = env_int("DATA_UPLOAD_MAX_MEMORY_SIZE", 12 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = env_int("FILE_UPLOAD_MAX_MEMORY_SIZE", 2 * 1024 * 1024)
PRIVATE_STORAGE_ROOT = env("PRIVATE_STORAGE_ROOT", str(BASE_DIR / "var" / "private"))
PRIVATE_STORAGE_BACKEND = env("PRIVATE_STORAGE_BACKEND", "local")
FILE_MAX_BYTES = env_int("FILE_MAX_BYTES", 10 * 1024 * 1024)
FILE_SCAN_BACKEND = env("FILE_SCAN_BACKEND", "noop")
# Optional clamd client configuration for a scanner on another host. The
# container entrypoint writes this file from CLAMD_HOST/CLAMD_PORT.
CLAMD_CONFIG_FILE = env("CLAMD_CONFIG_FILE", "")
FILE_DOWNLOAD_TOKEN_TTL = env_int("FILE_DOWNLOAD_TOKEN_TTL", 300)

# --- Payments -------------------------------------------------------------
PAYMENTS_ENABLED = env_bool("PAYMENTS_ENABLED", False)
PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = env("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_WEBHOOK_SECRET = env("PAYSTACK_WEBHOOK_SECRET", "")
PAYSTACK_BASE_CURRENCY = "NGN"

# --- Business policy defaults (require founder approval) ------------------
OTP_TTL_SECONDS = env_int("OTP_TTL_SECONDS", 600)
OTP_MAX_ATTEMPTS = env_int("OTP_MAX_ATTEMPTS", 5)
OTP_RESEND_COOLDOWN_SECONDS = env_int("OTP_RESEND_COOLDOWN_SECONDS", 60)
LOGIN_MAX_FAILURES = env_int("LOGIN_MAX_FAILURES", 5)
LOGIN_LOCK_SECONDS = env_int("LOGIN_LOCK_SECONDS", 900)
STEP_UP_MAX_AGE = env_int("STEP_UP_MAX_AGE", 600)
MFA_ISSUER = env("MFA_ISSUER", "B & A Essentials")
# Public derivation context, not a secret: the key material is SECRET_KEY.
MFA_ENC_CONTEXT = env("MFA_ENC_CONTEXT", "ba-essentials-mfa-v1")  # gitleaks:allow
STAFF_QUOTE_MAX_MINOR = env_int("STAFF_QUOTE_MAX_MINOR", 50_000_00)
QUOTE_VALIDITY_DAYS = env_int("QUOTE_VALIDITY_DAYS", 14)

LOGGING = build_logging(DEBUG)
