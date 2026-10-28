from __future__ import annotations

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from config.env import env

from .test import *  # noqa: F401,F403

_url = env("DATABASE_URL", "")
if not _url.startswith(("postgres://", "postgresql://")):
    raise ImproperlyConfigured(
        "config.settings.test_postgres requires DATABASE_URL to point at PostgreSQL."
    )

# Connections are deliberately not reused: each worker thread in the
# concurrency suite needs its own session and transaction visibility.
DATABASES = {
    "default": dj_database_url.parse(
        _url,
        conn_max_age=0,
        conn_health_checks=True,
    )
}
