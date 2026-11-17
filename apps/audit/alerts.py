"""Security alerts: tell a person when something in the audit trail looks like an attack.

Every alert goes by email (through the outbox) to SECURITY_ALERT_EMAILS. Alerts are
throttled: the same rule for the same person or source alerts at most once an hour, so
an attack can't flood the inbox. Nothing here may stop the action being audited: any
error is logged and swallowed.

Rules (first security audit, finding F7):
- a staff or admin account is locked after repeated wrong passwords;
- a staff or admin account turns off its second factor;
- 5 wrong authenticator codes for one account within 15 minutes;
- 3 refused file downloads by one account within an hour (someone trying other files);
- a staff role or admin access is granted, changed or removed;
- a payment notification with the wrong amount, or 5 with a bad signature or unknown reference in an hour;
- a backup or a restore check fails.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache

from apps.core import ratelimit

logger = logging.getLogger(__name__)

THROTTLE_SECONDS = 3600

# action -> (how many, within seconds, staff accounts only?)
COUNTED = {
    "auth.mfa_failed": (5, 900, False),
    "file.download_denied": (3, 3600, False),
    "billing.webhook_unknown_reference": (5, 3600, False),
    "billing.webhook_bad_signature": (5, 3600, False),
}
IMMEDIATE = {
    "auth.lockout": True,           # staff accounts only
    "auth.mfa_disabled": True,      # staff accounts only
    "staff.access_changed": False,
    "billing.webhook_amount_mismatch": False,
    "ops.backup_failed": False,
    "ops.restore_check_failed": False,
}

TITLES = {
    "auth.lockout": "A staff account was locked after repeated wrong passwords",
    "auth.mfa_disabled": "A staff account turned off its second factor",
    "auth.mfa_failed": "Repeated wrong authenticator codes",
    "file.download_denied": "Repeated refused file downloads",
    "staff.access_changed": "Staff or admin access changed",
    "billing.webhook_amount_mismatch": "A payment notification had the wrong amount",
    "billing.webhook_unknown_reference": "Payment notifications for unknown payments",
    "billing.webhook_bad_signature": "Payment notifications with a bad signature",
    "ops.backup_failed": "A backup failed",
    "ops.restore_check_failed": "The restore check failed",
}


def _is_staff(user) -> bool:
    return bool(user is not None and (getattr(user, "staff_role", "") or getattr(user, "is_staff", False)
                                      or getattr(user, "is_superuser", False)))


def on_audit(event) -> None:
    """Called for every audit event; most are not alerts."""
    try:
        _check(event.action, actor=event.actor, key=str(event.actor_id or event.resource_id or "-"),
               details={"resource": f"{event.resource_type} {event.resource_id}".strip(),
                        "result": event.result, "reason": event.reason, "request": event.correlation_id,
                        **(event.metadata or {})})
    except Exception:  # an alert must never break the audited action
        logger.exception("security_alert_failed action=%s", getattr(event, "action", "?"))


def count(action: str, key: str, details: dict | None = None) -> None:
    """For signals that aren't audit events (for example a webhook with a bad signature)."""
    try:
        _check(action, actor=None, key=key, details=details or {})
    except Exception:
        logger.exception("security_alert_failed action=%s", action)


def _check(action: str, *, actor, key: str, details: dict) -> None:
    if action in IMMEDIATE:
        if IMMEDIATE[action] and not _is_staff(actor):
            return
        _send(action, actor, key, details)
    elif action in COUNTED:
        limit, window, staff_only = COUNTED[action]
        if staff_only and not _is_staff(actor):
            return
        # ratelimit.hit is True while under the limit; the first False means the threshold was reached.
        if not ratelimit.hit(f"alert:{action}", key, limit=limit - 1, window_seconds=window):
            _send(action, actor, key, {**details, "threshold": f"{limit} within {window // 60} minutes"})


def _send(action: str, actor, key: str, details: dict) -> None:
    if not cache.add(f"alert-sent:{action}:{ratelimit.fingerprint(key)}", 1, timeout=THROTTLE_SECONDS):
        return  # already alerted about this within the hour
    title = TITLES.get(action, action)
    logger.warning("security_alert action=%s key=%s", action, key)
    recipients = list(getattr(settings, "SECURITY_ALERT_EMAILS", []) or [])
    if not recipients:
        return
    from apps.core.models import OutboxEvent

    who = getattr(actor, "email", "") or "-"
    lines = [f"Security alert: {title}", "", f"Account: {who}", f"Rule: {action}"]
    lines += [f"{name}: {value}" for name, value in details.items() if value not in ("", None)]
    lines += ["", ("Check the audit log in the staff app (Audit) for the full trail. "
                   "This alert won't repeat for the same account or source for an hour.")]
    OutboxEvent.objects.create(topic=OutboxEvent.Topic.EMAIL, payload={
        "to": recipients, "subject": f"[banda security] {title}"[:200], "body": "\n".join(lines),
    })
