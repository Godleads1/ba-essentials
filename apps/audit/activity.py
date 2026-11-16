"""Activity people can see about themselves (guidelines 4.9).

- Everyone sees their own sign-in, security and account activity.
- Business owners see their business's activity, including when someone at
  B & A opened their requests or files: by role, never by staff member's name.
"""
from __future__ import annotations

from apps.accounts.models import StaffRole

from .models import AuditEvent

# What each recorded action means, in words a client understands.
OWN_LABELS = {
    "auth.login": "Signed in",
    "auth.logout": "Signed out",
    "auth.lockout": "Sign-in paused after wrong codes",
    "auth.session_revoked": "Signed out a device",
    "auth.other_sessions_revoked": "Signed out of all other devices",
    "auth.email_changed": "Email address changed",
    "auth.phone_verified": "Phone number confirmed",
    "auth.phone_changed": "Phone number changed",
    "auth.phone_removed": "Phone number removed",
    "auth.code_channel_changed": "Changed how codes are sent",
    "auth.mfa_enrolled": "Authenticator app turned on",
    "auth.mfa_disabled": "Authenticator app turned off",
    "auth.mfa_failed": "Wrong authenticator code",
    "auth.password_changed": "Password changed",
    "account.details_changed": "Details updated",
    "account.preferences_changed": "Contact preferences changed",
    "account.consent_given": "Agreed",
    "account.consent_withdrawn": "Withdrew agreement",
    "account.closure_requested": "Asked to close the account",
    "account.closure_cancelled": "Kept the account open",
    "privacy.request_made": "Made a data request",
    "privacy.data_downloaded": "Downloaded your data",
}

CONSENT_WORDS = {"agreement": "to the Terms and Privacy Policy", "authority": "to act for the business",
                 "marketing": "to tips and offers"}

BUSINESS_LABELS = {
    "staff.task_opened": "opened request",
    "staff.list_viewed": "viewed",
    "file.downloaded": "downloaded a file",
    "file.uploaded": "uploaded a file",
    "message.posted": "sent a message",
    "task.cancelled_by_client": "cancelled request",
    "task.cancelled_by_staff": "cancelled request",
    "quote.accepted": "accepted a quote",
    "quote.rejected": "declined a quote",
    "deliverable.released": "released the work on",
    "support.opened": "opened support ticket",
    "support.replied": "replied on support ticket",
    "organization.member_joined": "joined the business",
    "billing.payment_verified": "payment confirmed",
    "account.closure_requested": "asked to close the account",
    "account.closure_cancelled": "kept the account open",
    "account.closed": "account closed",
}


def _detail(event) -> str:
    meta = event.metadata or {}
    if event.action.startswith("account.consent"):
        return CONSENT_WORDS.get(meta.get("kind", ""), "")
    if event.action == "auth.code_channel_changed":
        return {"sms": "by text message", "whatsapp": "by WhatsApp", "email": "by email"}.get(meta.get("channel", ""), "")
    if event.result != AuditEvent.Result.SUCCESS:
        return "Didn't go through"
    return ""


def own_activity(user, limit: int = 15) -> list[dict]:
    events = AuditEvent.objects.filter(actor=user, action__in=OWN_LABELS.keys()).order_by("-created_at")[:limit]
    return [{"at": e.created_at, "what": OWN_LABELS[e.action], "detail": _detail(e)} for e in events]


def _who(event, viewer) -> str:
    actor = event.actor
    if actor is None:
        return "B & A Essentials"
    if actor.pk == viewer.pk:
        return "You"
    role = actor.staff_role or ("administrator" if actor.is_superuser else "")
    if role or actor.is_staff:
        label = StaffRole(role).label if role in StaffRole.values else "Administrator"
        return f"B & A team · {label}"
    return actor.full_name or "A team member"


PAGE_WORDS = {"support_ticket": "your support ticket"}


def business_activity(organization, viewer, limit: int = 100) -> list[dict]:
    events = (
        AuditEvent.objects.filter(organization=organization, action__in=BUSINESS_LABELS.keys())
        .exclude(action="staff.list_viewed", resource_id__in=["clients", "queue", "dashboard"])
        .select_related("actor")
        .order_by("-created_at")[:limit]
    )
    rows = []
    for e in events:
        what = BUSINESS_LABELS[e.action]
        target = PAGE_WORDS.get(e.resource_id, "") if e.action == "staff.list_viewed" else ""
        if e.resource_type in ("task", "support_ticket") and e.resource_id:
            target = e.resource_id
        if e.action == "staff.task_opened" and (e.metadata or {}).get("view") == "money":
            what, target = "opened the invoices and fees on", e.resource_id
        rows.append({"at": e.created_at, "who": _who(e, viewer), "what": what, "target": target,
                     "staff": bool(e.actor and (e.actor.staff_role or e.actor.is_staff or e.actor.is_superuser))})
    return rows
