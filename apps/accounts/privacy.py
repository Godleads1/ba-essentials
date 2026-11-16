"""Data rights under the Nigeria Data Protection Act: requests and "download my data".

Requests go to the data protection contact, who replies within 30 days. Each
request and its outcome is in the audit log.
"""
from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit_service
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.core.sequences import next_value

from .models import (
    ConsentRecord,
    DataRequest,
    DataRequestKind,
    DataRequestStatus,
    User,
    UserSession,
)

REPLY_WITHIN = timedelta(days=30)
MAX_OPEN = 5
NEEDS_DETAILS = {DataRequestKind.CORRECT, DataRequestKind.OBJECT, DataRequestKind.MOVE,
                 DataRequestKind.WITHDRAW, DataRequestKind.REVIEW}


@transaction.atomic
def make_request(user: User, kind: str, details: str = "", organization=None) -> DataRequest:
    from apps.core.models import OutboxEvent

    if not user.is_client_account:
        raise PermissionFailure("Data requests are for client accounts.")
    if kind not in DataRequestKind.values:
        raise ValidationFailure("Choose what you'd like.")
    details = (details or "").strip()[:1000]
    if kind in NEEDS_DETAILS and not details:
        raise ValidationFailure("Tell us a little more so we can help.")
    if DataRequest.objects.filter(user=user, status=DataRequestStatus.OPEN).count() >= MAX_OPEN:
        raise ValidationFailure("You already have several open requests. We'll reply to those first.")
    request = DataRequest.objects.create(
        reference=f"DR-{next_value('data_request')}", user=user, organization=organization,
        kind=kind, details=details, reply_by=timezone.now() + REPLY_WITHIN,
    )
    audit_service.record(action="privacy.request_made", actor=user, organization=organization,
                         resource_type="data_request", resource_id=request.reference, metadata={"kind": kind})
    # The details stay in the app; the email says only that a request is waiting.
    OutboxEvent.objects.create(topic=OutboxEvent.Topic.EMAIL, payload={
        "to": [settings.DPO_EMAIL],
        "subject": f"Data request {request.reference}: {request.get_kind_display()}",
        "body": (f"A client made a data request ({request.reference}, {request.get_kind_display()}).\n"
                 f"Reply by {request.reply_by:%d %b %Y}.\n\nOpen it in the admin: "
                 f"{settings.APP_BASE_URL.rstrip('/')}/admin/accounts/datarequest/{request.pk}/change/\n"),
    })
    return request


@transaction.atomic
def resolve(request: DataRequest, staff_user, *, status: str, outcome: str) -> DataRequest:
    from apps.communications.models import NotificationKind
    from apps.communications.services import notify

    if not (staff_user.is_superuser or staff_user.has_perm("accounts.change_datarequest")):
        raise PermissionFailure("You can't handle data requests.")
    if status not in (DataRequestStatus.DONE, DataRequestStatus.DECLINED):
        raise ValidationFailure("Mark the request done, or that it couldn't be done.")
    outcome = (outcome or "").strip()
    if status == DataRequestStatus.DECLINED and not outcome:
        raise ValidationFailure("Explain why, so the client knows and can complain to the NDPC if they wish.")
    request = DataRequest.objects.select_for_update().get(pk=request.pk)
    if request.status != DataRequestStatus.OPEN:
        raise ValidationFailure("This request has already been answered.")
    request.status, request.outcome = status, outcome[:2000]
    request.resolved_at, request.resolved_by = timezone.now(), staff_user
    request.save(update_fields=["status", "outcome", "resolved_at", "resolved_by"])
    audit_service.record(action="privacy.request_answered", actor=staff_user, organization_id=request.organization_id,
                         resource_type="data_request", resource_id=request.reference,
                         metadata={"kind": request.kind, "status": status,
                                   "late": "yes" if request.resolved_at > request.reply_by else "no"})
    if request.user is not None:
        notify([request.user], organization_id=request.organization_id, kind=NotificationKind.PRIVACY,
               title=f"We've answered your data request {request.reference}")
    return request


# ---------- download my data ----------

def _ts(value):
    return value.isoformat() if value else None


def export_data(user: User) -> dict:
    """Everything we hold about this person and the businesses they belong to, that
    they're allowed to see. Files are listed by name; they download them in the app.
    Staff-only notes and other people's contact details are left out."""
    from apps.billing.models import Invoice
    from apps.communications.models import Notification, SupportTicket
    from apps.files.models import PrivateFile
    from apps.organizations.access import memberships_for
    from apps.tasks.models import Task

    data = {
        "exported_at": _ts(timezone.now()),
        "about": ("Your data from B & A Essentials. Files are listed by name; download them from the app. "
                  f"Questions: {settings.DPO_EMAIL}"),
        "account": {
            "full_name": user.full_name, "email": user.email, "email_confirmed_at": _ts(user.email_verified_at),
            "phone": user.phone, "phone_confirmed_at": _ts(user.phone_verified_at),
            "sign_in_codes_by": user.code_channel, "request_updates_by_email": user.updates_by_email,
            "tips_and_offers_by_email": user.marketing_by_email, "authenticator_app": user.mfa_enabled,
            "created_at": _ts(user.created_at),
        },
        "agreements": [
            {"what": c.get_kind_display(), "given": c.granted, "wording": c.wording, "version": c.version,
             "channel": c.channel, "at": _ts(c.created_at)}
            for c in ConsentRecord.objects.filter(user=user).order_by("created_at")
        ],
        "data_requests": [
            {"reference": r.reference, "what": r.get_kind_display(), "details": r.details, "status": r.get_status_display(),
             "made_at": _ts(r.created_at), "answered_at": _ts(r.resolved_at), "answer": r.outcome}
            for r in DataRequest.objects.filter(user=user).order_by("created_at")
        ],
        "signed_in_devices": [
            {"device": s.user_agent, "signed_in_at": _ts(s.created_at), "last_seen_at": _ts(s.last_seen_at),
             "signed_out_at": _ts(s.revoked_at)}
            for s in UserSession.objects.filter(user=user).order_by("created_at")
        ],
        "notifications": [
            {"title": n.title, "at": _ts(n.created_at), "read_at": _ts(n.read_at)}
            for n in Notification.objects.filter(user=user).order_by("created_at")
        ],
        "businesses": [],
    }
    for membership in memberships_for(user).select_related("organization"):
        org = membership.organization
        tasks = Task.objects.filter(organization=org).select_related("service_version").order_by("created_at")
        business = {
            "name": org.name, "country": org.country, "your_role": membership.get_role_display(),
            "requests": [
                {"reference": t.reference, "title": t.title, "brief": t.brief, "desired_outcome": t.desired_outcome,
                 "status": t.get_status_display(), "submitted_at": _ts(t.created_at), "completed_at": _ts(t.completed_at),
                 "information_you_gave": t.client_information,
                 "messages": [
                     {"from": m.get_author_role_display(), "name": m.author_name, "message": m.body, "at": _ts(m.created_at)}
                     for m in t.messages.filter(is_internal=False).order_by("created_at")
                 ]}
                for t in tasks
            ],
            "support": [
                {"reference": s.reference, "subject": s.subject, "status": s.get_status_display(), "opened_at": _ts(s.created_at),
                 "messages": [
                     {"from": m.get_author_role_display(), "name": m.author_name, "message": m.body, "at": _ts(m.created_at)}
                     for m in s.messages.filter(is_internal=False).order_by("created_at")
                 ]}
                for s in SupportTicket.objects.filter(organization=org).order_by("created_at")
            ],
            "files": [
                {"name": f.original_name, "size_bytes": f.size_bytes, "uploaded_at": _ts(f.created_at)}
                for f in PrivateFile.objects.filter(organization=org).order_by("created_at")
            ],
        }
        if membership.is_owner:
            business["invoices"] = [
                {"number": i.number, "amount": f"{i.amount_minor / 100:.2f} {i.currency}", "status": i.get_status_display(),
                 "issued_at": _ts(i.issued_at), "paid_at": _ts(i.paid_at)}
                for i in Invoice.objects.filter(organization=org).order_by("issued_at")
            ]
        data["businesses"].append(business)
    audit_service.record(action="privacy.data_downloaded", actor=user, resource_type="user", resource_id=str(user.pk))
    return data


def export_json(user: User) -> bytes:
    return json.dumps(export_data(user), cls=DjangoJSONEncoder, ensure_ascii=False, indent=2).encode("utf-8")
