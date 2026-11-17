"""Dates shown to people are in Lagos time (settings.TIME_ZONE), never UTC.

Stored times are UTC. A time after 23:00 UTC is already the next day in Lagos,
so formatting a stored time directly can show the wrong day in an email or message.
"""
from __future__ import annotations

from datetime import datetime

from django.utils import timezone


def local(value):
    """An aware datetime in Lagos time; plain dates, naive datetimes and None pass through."""
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value
