from __future__ import annotations

from django.db import transaction

from .models import Counter


def next_value(key: str, *, start: int = 1000) -> int:
    """Allocate the next value for a counter. Call inside a transaction."""
    with transaction.atomic():
        counter, created = Counter.objects.select_for_update().get_or_create(
            key=key, defaults={"value": start - 1}
        )
        counter.value += 1
        counter.save(update_fields=["value"])
        return counter.value
