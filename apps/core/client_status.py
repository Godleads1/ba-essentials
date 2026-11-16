"""How a request's status reads to a client (the banda design's status guide).

Staff see the detailed workflow statuses; clients see a short, plain set. Some
internal steps (for example "rework required") are never shown to clients.
"""
from __future__ import annotations

# group: (label, chip classes, what it means, dot colour for the status guide)
GROUPS = {
    "submitted": ("Submitted", "bg-cream text-ink", "Received and waiting in the queue", "bg-ink-soft"),
    "scoping": ("Being scoped", "border border-brand-navy/40 text-brand-navy", "We're confirming what's needed and any cost", "bg-brand-lavender"),
    "progress": ("In progress", "bg-pill-navyBg text-brand-navy", "A specialist is working on it", "bg-brand-navy"),
    "input": ("Needs your info", "bg-pill-orangeBg text-brand-orangeDark", "We need something from you to continue", "bg-brand-orange"),
    "checking": ("Being checked", "bg-[#EFE9FB] text-[#5B3FA8]", "Quality check before it reaches you", "bg-[#7C5CC4]"),
    "hold": ("On hold", "bg-[#F1EFEC] text-ink-muted", "Paused for now", "bg-[#B5ADA5]"),
    "delivered": ("Delivered", "bg-pill-greenBg text-pill-greenTx", "Finished and sent to you", "bg-pill-greenTx"),
    "cancelled": ("Cancelled", "bg-[#FDECEC] text-[#B42318]", "Stopped before work began", "bg-[#B42318]"),
}

# Category colours, in the preview's order; a category gets one by its position.
CATEGORY_TONES = [
    "bg-pill-navyBg text-brand-navy", "bg-pill-orangeBg text-brand-orangeDark", "bg-pill-greenBg text-pill-greenTx",
    "bg-[#EFE9FB] text-[#5B3FA8]", "bg-cream text-ink",
]

PRIORITY = {"high": ("Urgent", "bg-pill-orangeBg text-brand-orangeDark"),
            "medium": ("Standard", "bg-pill-navyBg text-brand-navy"),
            "low": ("Low", "bg-cream text-ink-muted")}

# workflow status -> (group, label override)
STATUS = {
    "draft": ("submitted", ""),
    "submitted": ("submitted", ""),
    "triage": ("submitted", ""),
    "needs_information": ("input", ""),
    "scoping": ("scoping", ""),
    "awaiting_client_approval": ("input", "Quote to approve"),
    "awaiting_payment": ("input", "Payment needed"),
    "ready_for_assignment": ("progress", ""),
    "assigned": ("progress", ""),
    "in_progress": ("progress", ""),
    "rework_required": ("progress", ""),
    "internal_review": ("checking", ""),
    "approved_for_release": ("checking", ""),
    "delivered": ("delivered", "Delivered: check it"),
    "revision_requested": ("progress", "Changes in progress"),
    "completed": ("delivered", "Completed"),
    "on_hold": ("hold", ""),
    "cancelled": ("cancelled", ""),
}

# What the client is asked to do, for statuses waiting on them.
ACTION = {
    "needs_information": "Answer the team's question",
    "awaiting_client_approval": "Review and approve the quote",
    "awaiting_payment": "Pay to start the work",
    "delivered": "Check the delivered work",
}


def group(status: str) -> str:
    return STATUS.get(str(status), ("submitted", ""))[0]


def label(status: str) -> str:
    g, override = STATUS.get(str(status), ("submitted", ""))
    return override or GROUPS[g][0]


def chip(status: str) -> str:
    return GROUPS[group(status)][1]


def statuses_in(*groups: str) -> list[str]:
    return [s for s, (g, _) in STATUS.items() if g in groups]


def guide() -> list[dict]:
    return [{"label": v[0], "about": v[2], "dot": v[3]} for v in GROUPS.values()]
