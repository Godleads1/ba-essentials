from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction

from .exceptions import IdempotencyConflict
from .models import IdempotencyKey
from .ratelimit import fingerprint


@dataclass(frozen=True)
class IdempotentResult:
    reference: str
    replayed: bool


def reserve(*, user, scope: str, key: str, request_fingerprint: str) -> IdempotentResult:
    """Claims an idempotency key. Raises IdempotencyConflict on reuse.

    The caller performs its work inside the surrounding transaction and calls
    `complete` with the resulting reference. A second request with the same key
    either replays the stored reference or is rejected if it carries a
    different request fingerprint.
    """
    if not key or len(key) > 128:
        raise IdempotencyConflict("A valid idempotency key is required.")
    try:
        with transaction.atomic():
            record = IdempotencyKey.objects.create(
                user=user,
                scope=scope,
                key=key,
                request_hash=fingerprint(request_fingerprint),
            )
    except IntegrityError:
        existing = IdempotencyKey.objects.filter(user=user, scope=scope, key=key).first()
        if existing is None:
            raise
        if existing.request_hash != fingerprint(request_fingerprint):
            raise IdempotencyConflict(
                "This action was already submitted with different details."
            )
        if not existing.response_ref:
            raise IdempotencyConflict("This action is already being processed.")
        return IdempotentResult(reference=existing.response_ref, replayed=True)
    return IdempotentResult(reference=str(record.pk), replayed=False)


def complete(*, user, scope: str, key: str, reference: str) -> None:
    IdempotencyKey.objects.filter(user=user, scope=scope, key=key).update(
        response_ref=reference
    )
