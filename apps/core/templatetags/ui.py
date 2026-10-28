from __future__ import annotations

from django import template

register = template.Library()

STATUS_LABELS = {
    "draft": "Draft",
    "submitted": "Submitted",
    "triage": "Triage",
    "needs_information": "Needs information",
    "scoping": "Scoping",
    "awaiting_client_approval": "Awaiting approval",
    "awaiting_payment": "Awaiting payment",
    "ready_for_assignment": "Ready",
    "assigned": "Assigned",
    "in_progress": "In progress",
    "internal_review": "In review",
    "rework_required": "Rework required",
    "approved_for_release": "Approved",
    "delivered": "Delivered",
    "revision_requested": "Revision requested",
    "completed": "Completed",
    "on_hold": "On hold",
    "cancelled": "Cancelled",
    # Invoice / quote / review statuses share the same vocabulary.
    "issued": "Issued",
    "paid": "Paid",
    "void": "Void",
    "sent": "Sent",
    "accepted": "Accepted",
    "rejected": "Rejected",
    "superseded": "Superseded",
    "expired": "Expired",
    "pending_review": "Pending review",
    "approved": "Approved",
    "released": "Released",
    "active": "Active",
    "available": "Available",
    "scoping_only": "Scoping only",
    "unavailable": "Unavailable",
}

STATUS_TONES = {
    "draft": "bg-slate-50 text-slate-600 ring-slate-200",
    "submitted": "bg-sky-50 text-sky-700 ring-sky-200",
    "triage": "bg-sky-50 text-sky-700 ring-sky-200",
    "needs_information": "bg-amber-50 text-amber-700 ring-amber-200",
    "scoping": "bg-indigo-50 text-indigo-700 ring-indigo-200",
    "awaiting_client_approval": "bg-amber-50 text-amber-700 ring-amber-200",
    "awaiting_payment": "bg-orange-50 text-orange-700 ring-orange-200",
    "ready_for_assignment": "bg-violet-50 text-violet-700 ring-violet-200",
    "assigned": "bg-violet-50 text-violet-700 ring-violet-200",
    "in_progress": "bg-blue-50 text-blue-700 ring-blue-200",
    "internal_review": "bg-cyan-50 text-cyan-700 ring-cyan-200",
    "rework_required": "bg-rose-50 text-rose-700 ring-rose-200",
    "approved_for_release": "bg-emerald-50 text-emerald-700 ring-emerald-200",
    "delivered": "bg-brand-50 text-brand-700 ring-brand-200",
    "revision_requested": "bg-amber-50 text-amber-700 ring-amber-200",
    "completed": "bg-brand-50 text-brand-700 ring-brand-200",
    "on_hold": "bg-slate-100 text-slate-600 ring-slate-300",
    "cancelled": "bg-slate-100 text-slate-500 ring-slate-300",
    "issued": "bg-amber-50 text-amber-700 ring-amber-200",
    "paid": "bg-brand-50 text-brand-700 ring-brand-200",
    "released": "bg-brand-50 text-brand-700 ring-brand-200",
    "pending_review": "bg-cyan-50 text-cyan-700 ring-cyan-200",
    "approved": "bg-emerald-50 text-emerald-700 ring-emerald-200",
    "active": "bg-brand-50 text-brand-700 ring-brand-200",
    "available": "bg-brand-50 text-brand-700 ring-brand-200",
    "scoping_only": "bg-amber-50 text-amber-700 ring-amber-200",
    "unavailable": "bg-slate-100 text-slate-500 ring-slate-300",
}

ACTION_LABELS = {
    "triage": "Start triage",
    "request_information": "Request information",
    "provide_information": "Send information",
    "scope": "Begin scoping",
    "send_quote": "Send quote",
    "assign": "Assign specialist",
    "accept_assignment": "Accept assignment",
    "submit_for_review": "Submit for review",
    "approve_deliverable": "Approve",
    "request_rework": "Request rework",
    "resume_work": "Resume work",
    "release": "Release to client",
    "client_accept": "Accept deliverable",
    "client_request_revision": "Request revision",
    "start_revision": "Start revision",
    "hold": "Put on hold",
    "resume": "Resume",
    "cancel_client": "Cancel request",
    "cancel_staff": "Cancel request",
}


@register.filter
def status_label(value):
    return STATUS_LABELS.get(str(value), str(value).replace("_", " ").title())


@register.filter
def status_tone(value):
    return STATUS_TONES.get(str(value), "bg-slate-50 text-slate-600 ring-slate-200")


@register.filter
def action_label(value):
    return ACTION_LABELS.get(str(value), str(value).replace("_", " ").title())


@register.filter
def money_minor(value, currency="NGN"):
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return "—"
    return f"{currency} {amount / 100:,.2f}"


@register.filter
def initials(value):
    parts = [part for part in str(value or "").replace("@", " ").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()
