"""Staging settings.

Production's fail-closed checks run first. Render supplies the public
hostname at runtime; adopting it keeps absolute links and CSRF checks correct
even if the service name received a suffix during provisioning.
"""

from __future__ import annotations

import re

from config.env import env

from .production import *  # noqa: F401,F403

_render_host = env("RENDER_EXTERNAL_HOSTNAME", "").strip()
if re.fullmatch(r"[a-z0-9.-]+", _render_host or ""):
    _origin = f"https://{_render_host}"
    APP_BASE_URL = _origin
    ALLOWED_HOSTS = sorted({*ALLOWED_HOSTS, _render_host})
    CSRF_TRUSTED_ORIGINS = sorted({*CSRF_TRUSTED_ORIGINS, _origin})
