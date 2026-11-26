from __future__ import annotations

import re

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

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
    "apps.communications",
    "apps.website",
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
                "apps.website.context_processors.site_menu",
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
    # Only the first 5 characters of the password's SHA-1 hash leave the server (apps/accounts/breached.py).
    {"NAME": "apps.accounts.breached.BreachedPasswordValidator"},
]
BREACHED_PASSWORD_CHECK = env_bool("BREACHED_PASSWORD_CHECK", True)

# Audit events can keep the visitor's IP address, which helps trace an attack. It is personal
# data, so it stays OFF until the Privacy Policy says so (see docs/SECURITY.md, "Audit trail").
# When on, addresses are cleared after AUDIT_IP_RETENTION_DAYS by run_retention.
AUDIT_STORE_IP = env_bool("AUDIT_STORE_IP", False)
AUDIT_IP_RETENTION_DAYS = env_int("AUDIT_IP_RETENTION_DAYS", 90)

# Superusers nobody can delete, demote or switch off from the app (apps/accounts/protection.py).
# Emails, comma-separated. Set only on the server: never put real people's emails in the code.
PROTECTED_SUPERUSER_EMAILS = [e.lower() for e in env_list("PROTECTED_SUPERUSER_EMAILS")]

# Where Django admin lives. Moving it off /admin/ stops the automated scans that try that address;
# it still needs a staff sign-in with an authenticator code. Letters, digits, - and _ only.
ADMIN_URL = env("ADMIN_URL", "admin").strip("/") + "/"
if not re.fullmatch(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*/", ADMIN_URL):
    raise ImproperlyConfigured("ADMIN_URL may only contain letters, digits, - and _ (for example ops-desk).")

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
# "Keep me signed in on this device" (clients only): the sign-in survives closing the
# browser for up to 30 days, and ends after 7 days without use (defaults; change them
# here). Without it, the sign-in
# ends when the browser closes, or after the normal idle timeout. Staff never get it.
REMEMBER_ME_SECONDS = env_int("REMEMBER_ME_SECONDS", 60 * 60 * 24 * 30)
REMEMBER_ME_IDLE_TIMEOUT = env_int("REMEMBER_ME_IDLE_TIMEOUT", 60 * 60 * 24 * 7)
LOGIN_URL = "/login"
LOGIN_REDIRECT_URL = "/app/"
LOGOUT_REDIRECT_URL = "/login"

# --- Security headers and transport --------------------------------------
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Which visitor address to trust for rate limits (apps/core/client_ip.py). Never the
# left-hand X-Forwarded-For entries, which any visitor can write.
CLIENT_IP_HEADER = env("CLIENT_IP_HEADER", "")
TRUSTED_PROXY_COUNT = env_int("TRUSTED_PROXY_COUNT", 0)
# Who gets security alerts (apps/audit/alerts.py), comma-separated.
SECURITY_ALERT_EMAILS = env_list("SECURITY_ALERT_EMAILS")
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Flutterwave's test checkout lives on its own host. It is allowed only while a test key is
# configured (the same rule as apps/billing/flutterwave.py allowed_checkout_hosts).
_FLUTTERWAVE_TEST_FORM_ACTION = (
    " https://checkout-v2.dev-flutterwave.com"
    if env("FLUTTERWAVE_SECRET_KEY", "").startswith("FLWSECK_TEST-")
    else ""
)

CONTENT_SECURITY_POLICY = env(
    "CONTENT_SECURITY_POLICY",
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    # The checkout buttons post to this site, which then sends the browser on to the payment
    # page. Browsers apply form-action to that redirect too, so the checkout hosts must be listed
    # (apps/billing/paystack.py and flutterwave.py CHECKOUT_HOSTS; checked by banda.W005).
    "base-uri 'self'; form-action 'self' https://checkout.paystack.com "
    "https://checkout.flutterwave.com https://checkout-v3.flutterwave.com"
    + _FLUTTERWAVE_TEST_FORM_ACTION,
)

# --- Legal entity ---------------------------------------------------------
# Who the client contracts with, shown in the Terms (1.1) and Privacy Policy. When the business
# is incorporated, change these two in .env only, the day the CAC certificate is issued, for
# example "B&A Essentials Ltd" and "a private company limited by shares, registered in Nigeria
# under RC 1234567". Never show "Ltd" or an RC number before then.
LEGAL_ENTITY_NAME = env("LEGAL_ENTITY_NAME", "B&A Essential Services")
LEGAL_ENTITY_DESCRIPTION = env(
    "LEGAL_ENTITY_DESCRIPTION",
    "a business name registered in Nigeria with the Corporate Affairs Commission under number BN 74100773",
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
# Cloudflare R2 (PRIVATE_STORAGE_BACKEND=r2): a private bucket and an API token limited to it.
R2_ENDPOINT_URL = env("R2_ENDPOINT_URL", "")  # https://<account id>.r2.cloudflarestorage.com
R2_BUCKET = env("R2_BUCKET", "")
R2_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", "")
# Backups (apps/core/backups.py): "local" (BACKUP_DIR) or "r2" (a separate private bucket).
BACKUP_BACKEND = env("BACKUP_BACKEND", "local")
BACKUP_DIR = env("BACKUP_DIR", str(BASE_DIR / "var" / "backups"))
BACKUP_R2_BUCKET = env("BACKUP_R2_BUCKET", "")
BACKUP_R2_ACCESS_KEY_ID = env("BACKUP_R2_ACCESS_KEY_ID", "")
BACKUP_R2_SECRET_ACCESS_KEY = env("BACKUP_R2_SECRET_ACCESS_KEY", "")
BACKUP_ENCRYPTION_KEY = env("BACKUP_ENCRYPTION_KEY", "")
BACKUP_KEEP_HOURS = env_int("BACKUP_KEEP_HOURS", 48)  # Privacy Policy: rotated within two days
RESTORE_CHECK_DATABASE_URL = env("RESTORE_CHECK_DATABASE_URL", "")
FILE_MAX_BYTES = env_int("FILE_MAX_BYTES", 10 * 1024 * 1024)
# Total a business's own uploads may use (0 = no limit). Staff uploads don't count.
ORG_STORAGE_LIMIT_BYTES = env_int("ORG_STORAGE_LIMIT_BYTES", 2 * 1024 ** 3)
FILE_SCAN_BACKEND = env("FILE_SCAN_BACKEND", "noop")
# Optional clamd client configuration for a scanner on another host. The
# container entrypoint writes this file from CLAMD_HOST/CLAMD_PORT.
CLAMD_CONFIG_FILE = env("CLAMD_CONFIG_FILE", "")
FILE_DOWNLOAD_TOKEN_TTL = env_int("FILE_DOWNLOAD_TOKEN_TTL", 300)

# --- Payments -------------------------------------------------------------
PAYMENTS_ENABLED = env_bool("PAYMENTS_ENABLED", False)
# Starting a checkout is limited per account and per connection, so a stolen card can't be
# tried over and over (card testing). Paystack and Flutterwave have their own checks as well.
CHECKOUT_STARTS_PER_ACCOUNT_PER_HOUR = env_int("CHECKOUT_STARTS_PER_ACCOUNT_PER_HOUR", 10)
CHECKOUT_STARTS_PER_IP_PER_HOUR = env_int("CHECKOUT_STARTS_PER_IP_PER_HOUR", 30)
# Emergency switch: SIGNUPS_PAUSED=true stops new client accounts (existing clients still sign in).
SIGNUPS_PAUSED = env_bool("SIGNUPS_PAUSED", False)
PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY", "")
PAYSTACK_PUBLIC_KEY = env("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_WEBHOOK_SECRET = env("PAYSTACK_WEBHOOK_SECRET", "")
PAYSTACK_BASE_CURRENCY = "NGN"
# Flutterwave takes US dollar payments from businesses outside Nigeria. The secret
# key and webhook hash come from the environment and are never sent to the browser.
FLUTTERWAVE_SECRET_KEY = env("FLUTTERWAVE_SECRET_KEY", "")
FLUTTERWAVE_PUBLIC_KEY = env("FLUTTERWAVE_PUBLIC_KEY", "")
FLUTTERWAVE_WEBHOOK_HASH = env("FLUTTERWAVE_WEBHOOK_HASH", "")

# --- Business policy defaults (require founder approval) ------------------
# A code lasts 5 minutes and allows 3 tries (security checklist 15).
OTP_TTL_SECONDS = env_int("OTP_TTL_SECONDS", 300)
OTP_MAX_ATTEMPTS = env_int("OTP_MAX_ATTEMPTS", 3)
OTP_RESEND_COOLDOWN_SECONDS = env_int("OTP_RESEND_COOLDOWN_SECONDS", 60)
LOGIN_MAX_FAILURES = env_int("LOGIN_MAX_FAILURES", 5)
LOGIN_LOCK_SECONDS = env_int("LOGIN_LOCK_SECONDS", 900)

# One-time codes by SMS and WhatsApp (Termii). Without these, clients get codes by email.
# The base URL is the one shown in your Termii dashboard; WhatsApp needs Termii to
# switch on its WhatsApp OTP route and a WhatsApp device name.
SUPPORT_EMAIL = env("SUPPORT_EMAIL", "support@bandaessentials.com")

# Search engines (apps/website/seo.py). APP_BASE_URL above is the public address used in
# canonical links and the sitemap: set it to the address people should land on,
# e.g. https://bandaessentials.com (with or without www, but always the same one).
GOOGLE_SITE_VERIFICATION = env("GOOGLE_SITE_VERIFICATION", "")  # the content="" value from Search Console
BING_SITE_VERIFICATION = env("BING_SITE_VERIFICATION", "")  # from Bing Webmaster Tools
# Optional business details for Google (shown only when set): profiles on other sites
# (comma-separated URLs: LinkedIn, Instagram, X, Facebook), a phone number and the city.
SEO_SAME_AS = env_list("SEO_SAME_AS")
BUSINESS_PHONE = env("BUSINESS_PHONE", "")
BUSINESS_CITY = env("BUSINESS_CITY", "")
BUSINESS_REGION = env("BUSINESS_REGION", "")
# The data protection contact (Privacy Policy). Data requests are sent here.
DPO_EMAIL = env("DPO_EMAIL", "dpo@bandaessentials.com")
TERMII_API_KEY = env("TERMII_API_KEY", "")
TERMII_BASE_URL = env("TERMII_BASE_URL", "")
TERMII_SENDER_ID = env("TERMII_SENDER_ID", "")
TERMII_WHATSAPP_FROM = env("TERMII_WHATSAPP_FROM", "")
TERMII_SMS_CHANNEL = env("TERMII_SMS_CHANNEL", "dnd")
# Text messages and WhatsApp cost money per message, so they are limited (apps/accounts/termii.py):
# - only to numbers in these countries (ISO codes, comma-separated); other numbers use email;
# - at most this many across the whole site per minute and per hour, with a security alert when
#   either is reached (an attacker sending codes to many numbers to run up the bill);
# - TEXT_MESSAGES_PAUSED=true stops them all at once, and codes go by email instead.
TEXT_COUNTRIES = [c.upper() for c in env_list("TEXT_COUNTRIES", ("NG",))]
TEXT_SENDS_PER_MINUTE = env_int("TEXT_SENDS_PER_MINUTE", 30)
TEXT_SENDS_PER_HOUR = env_int("TEXT_SENDS_PER_HOUR", 300)
TEXT_MESSAGES_PAUSED = env_bool("TEXT_MESSAGES_PAUSED", False)
TERMII_WHATSAPP_CHANNEL = env("TERMII_WHATSAPP_CHANNEL", "whatsapp_otp")
# Code requests answer no faster than this, so the timing never shows whether an
# account exists (sending a real code takes longer than doing nothing).
AUTH_MIN_RESPONSE_SECONDS = float(env("AUTH_MIN_RESPONSE_SECONDS", "1.2"))
# Each lockout after repeated wrong codes or passwords lasts twice as long as the last.
LOGIN_LOCK_MAX_SECONDS = env_int("LOGIN_LOCK_MAX_SECONDS", 60 * 60 * 24)
STEP_UP_MAX_AGE = env_int("STEP_UP_MAX_AGE", 600)
MFA_ISSUER = env("MFA_ISSUER", "B & A Essentials")
# Public derivation context, not a secret: the key material is SECRET_KEY.
MFA_ENC_CONTEXT = env("MFA_ENC_CONTEXT", "ba-essentials-mfa-v1")  # gitleaks:allow
# Fernet key for authenticator secrets, separate from SECRET_KEY (apps/accounts/mfa.py).
# Make one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MFA_ENCRYPTION_KEY = env("MFA_ENCRYPTION_KEY", "")
STAFF_QUOTE_MAX_MINOR = env_int("STAFF_QUOTE_MAX_MINOR", 50_000_00)
QUOTE_VALIDITY_DAYS = env_int("QUOTE_VALIDITY_DAYS", 14)

LOGGING = build_logging(DEBUG)
