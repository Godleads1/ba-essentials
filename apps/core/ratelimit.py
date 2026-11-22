from __future__ import annotations

import hashlib

from django.core.cache import cache

from .exceptions import RateLimited


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hit(scope: str, identifier: str, *, limit: int, window_seconds: int) -> bool:
    """Fixed-window counter. Returns True when the caller is still under limit.

    Cache-backed rather than DB-backed so an unauthenticated flood cannot grow
    the database. Production must use a shared cache; settings enforce that.
    """
    key = f"rl:{scope}:{fingerprint(identifier)}"
    added = cache.add(key, 1, timeout=window_seconds)
    if added:
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window_seconds)
        return True
    return count <= limit


def used(scope: str, identifier: str) -> int:
    """How many hits the current window has had, without adding one."""
    try:
        return int(cache.get(f"rl:{scope}:{fingerprint(identifier)}") or 0)
    except (TypeError, ValueError):
        return 0


def enforce(scope: str, identifier: str, *, limit: int, window_seconds: int) -> None:
    if not hit(scope, identifier, limit=limit, window_seconds=window_seconds):
        raise RateLimited("Too many attempts. Please wait and try again.")
