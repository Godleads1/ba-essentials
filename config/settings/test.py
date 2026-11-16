import tempfile

from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-only-secret-key-not-for-any-real-deployment"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ba-tests",
    }
}

PRIVATE_STORAGE_ROOT = tempfile.mkdtemp(prefix="ba-private-tests-")
STATIC_ROOT = tempfile.mkdtemp(prefix="ba-static-tests-")
FILE_SCAN_BACKEND = "noop"
OTP_RESEND_COOLDOWN_SECONDS = 0
STEP_UP_MAX_AGE = 600

# Deterministic secrets for tests; never used outside the test settings.
PAYSTACK_SECRET_KEY = "sk_test_webhook_secret"
PAYSTACK_WEBHOOK_SECRET = "sk_test_webhook_secret"
FLUTTERWAVE_SECRET_KEY = "FLWSECK_TEST-synthetic"
FLUTTERWAVE_WEBHOOK_HASH = "synthetic-flutterwave-hash"
PAYMENTS_ENABLED = True

AUTH_MIN_RESPONSE_SECONDS = 0
TERMII_API_KEY = "termii-test-key"
TERMII_BASE_URL = "https://termii.example.test"
TERMII_SENDER_ID = "BandA"
TERMII_WHATSAPP_FROM = "banda-device"
