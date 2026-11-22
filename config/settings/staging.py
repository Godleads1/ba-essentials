"""Staging settings: production's settings and checks, for a copy of the site with synthetic data.

Kept as its own module so a staging server can be told apart in logs and error reports.
"""

from __future__ import annotations

from .production import *
