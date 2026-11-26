"""Business hours for support targets (Terms 1.5 and 3.6).

Support runs 9am to 5pm West Africa Time, Monday to Friday, except Nigerian public
holidays. Holidays change every year (some follow the moon), so they are listed in
the SUPPORT_HOLIDAYS setting as ISO dates and updated by staff each year.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

OPEN = time(9, 0)
CLOSE = time(17, 0)
HOURS_PER_DAY = 8


def _holidays() -> set[date]:
    days = set()
    for raw in getattr(settings, "SUPPORT_HOLIDAYS", ()) or ():
        try:
            days.add(date.fromisoformat(str(raw).strip()))
        except ValueError:
            continue
    return days


def is_business_day(day: date, holidays: set[date] | None = None) -> bool:
    holidays = _holidays() if holidays is None else holidays
    return day.weekday() < 5 and day not in holidays


def add_business_hours(start: datetime, hours: float) -> datetime:
    """The moment `hours` of support time have passed after `start` (an aware datetime)."""
    holidays = _holidays()
    tz = timezone.get_current_timezone()
    current = timezone.localtime(start, tz)
    remaining = timedelta(hours=hours)
    for _ in range(3660):  # a safety stop: about ten years of days
        day = current.date()
        opens = datetime.combine(day, OPEN, tzinfo=current.tzinfo)
        closes = datetime.combine(day, CLOSE, tzinfo=current.tzinfo)
        if not is_business_day(day, holidays) or current >= closes:
            current = datetime.combine(day + timedelta(days=1), OPEN, tzinfo=current.tzinfo)
            continue
        current = max(current, opens)
        available = closes - current
        if remaining <= available:
            return current + remaining
        remaining -= available
        current = datetime.combine(day + timedelta(days=1), OPEN, tzinfo=current.tzinfo)
    raise ValueError("Couldn't find enough business hours.")
