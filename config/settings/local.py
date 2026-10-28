from config.env import env_bool

from .base import *  # noqa: F401,F403

DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

# Local development keeps the console email backend so OTP codes are visible
# in the terminal. Production fails closed if this backend is in use.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
