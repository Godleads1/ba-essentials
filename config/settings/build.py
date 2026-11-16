"""Settings for collecting hashed static files inside the container build.

No database, cache, mail or secret is contacted. The runtime uses
config.settings.staging, which configures the same manifest storage so the
hashed asset names generated here match what templates request.
"""

from __future__ import annotations

from .base import *  # noqa: F401,F403

DEBUG = False

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": STATICFILES_STORAGE},
}
