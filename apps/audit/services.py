from __future__ import annotations

from django.utils import timezone

from apps.core.context import get_correlation_id

from .models import AuditEvent

SENSITIVE_KEY_PARTS = ("password", "secret", "token", "otp", "code", "mfa", "key")

MAX_METADATA_KEYS = 20


def _safe_metadata(metadata: dict | None) -> dict:
    if not metadata:
        return {}
    safe: dict = {}
    for index, (key, value) in enumerate(metadata.items()):
        if index >= MAX_METADATA_KEYS:
            break
        lowered = str(key).lower()
        if any(part in lowered for part in SENSITIVE_KEY_PARTS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)[:64]] = value if not isinstance(value, str) else value[:200]
    return safe


def record(
    *,
    action: str,
    actor=None,
    organization=None,
    organization_id=None,
    resource_type: str = "",
    resource_id: str = "",
    result: str = AuditEvent.Result.SUCCESS,
    reason: str = "",
    metadata: dict | None = None,
) -> AuditEvent:
    """Write an audit event. Call inside the transaction that performs the action."""
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        actor_type="user" if getattr(actor, "is_authenticated", False) else "system",
        organization_id=organization_id or getattr(organization, "pk", None),
        action=action[:64],
        resource_type=resource_type[:64],
        resource_id=str(resource_id)[:64],
        result=result,
        reason=(reason or "")[:255],
        correlation_id=get_correlation_id()[:64],
        metadata=_safe_metadata(metadata),
    )


def purge_older_than(days: int) -> int:
    cutoff = timezone.now() - timezone.timedelta(days=days)
    deleted, _ = AuditEvent.objects.filter(created_at__lt=cutoff).delete()
    return deleted
