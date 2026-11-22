from __future__ import annotations

STAFF_NAV = (
    ("staff-dashboard", "Dashboard", "M3 12l9-9 9 9M5 10v10h14V10"),
    ("staff-my-cases", "My Cases", "M6 3h12v18l-6-4-6 4z"),
    ("staff-queue", "Operations Queue", "M4 6h16M4 12h16M4 18h10"),
    ("staff-needs-information", "Needs Information", "M12 18h.01M9.1 9a3 3 0 015.8 1c0 2-3 2-3 4"),
    ("staff-scoping", "Scoping & Quotes", "M7 3h7l4 4v14H7zM14 3v5h5"),
    ("staff-workload", "Assignments", "M16 11a4 4 0 10-8 0 4 4 0 008 0zM3 21a9 9 0 0118 0"),
    ("staff-deliverables", "Deliverables", "M14 3v5h5M6 3h9l4 4v14H6z"),
    ("staff-reviews", "Review Queue", "M9 12l2 2 4-4M12 3l9 4v5c0 5-4 8-9 9-5-1-9-4-9-9V7l9-4z"),
    ("staff-support", "Support", "M21 15a4 4 0 01-4 4H8l-5 3V7a4 4 0 014-4h10a4 4 0 014 4z"),
    ("staff-clients", "Clients", "M16 11a4 4 0 10-8 0 4 4 0 008 0zM3 21a9 9 0 0118 0"),
    ("staff-finance", "Finance", "M12 3v18M7 7h7a3 3 0 010 6H7h8a3 3 0 010 6H7"),
    ("staff-catalogue", "Service Catalogue", "M4 5h16v4H4zM4 15h16v4H4z"),
    ("staff-website-pages", "Website", "M12 3a9 9 0 100 18 9 9 0 000-18zM3 12h18M12 3c2.5 2.6 3.8 5.7 3.8 9S14.5 18.4 12 21c-2.5-2.6-3.8-5.7-3.8-9S9.5 5.6 12 3z"),
    ("staff-leads", "Contact Inbox", "M3 6h18v12H3zM3 8l9 6 9-6"),
    ("staff-audit", "Audit Log", "M4 5h16v14H4zM8 9h8M8 13h5"),
)

STAFF_NAV_PERMISSIONS = {
    "staff-my-cases": None,
    "staff-needs-information": "task.triage",
    "staff-scoping": "task.scope",
    "staff-workload": "task.assign",
    "staff-deliverables": "files.view_all",
    "staff-reviews": "review.decide",
    "staff-support": "support.handle",
    "staff-clients": "client.view",
    "staff-finance": "finance.view",
    "staff-catalogue": "catalogue.manage",
    "staff-website-pages": "website.edit",
    "staff-leads": "leads.view",
    "staff-audit": "audit.view",
}

# The Website admin menu: (url name, label, icon path, section). Shown in the Website area,
# and always for website-only staff. A "#" in the url name adds an anchor.
WEBSITE_NAV = (
    ("staff-website-pages", "Pages", "M14 3v5h5M6 3h9l4 4v14H6zM9 13h6M9 17h4", "pages"),
    ("staff-website-home", "Home page", "M3 12l9-9 9 9M5 10v10h14V10", "home"),
    ("staff-website-home#faqs", "Questions (FAQs)", "M12 18h.01M9.1 9a3 3 0 015.8 1c0 2-3 2-3 4M12 3a9 9 0 100 18 9 9 0 000-18z", "faqs"),
    ("staff-website-images", "Media library", "M4 5h16v14H4zM4 15l4-4 4 4 3-3 5 5M15 9h.01", "media"),
    ("staff-website-seo", "SEO & meta", "M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-5-5", "seo"),
    ("staff-website-pricing", "Pricing", "M12 3v18M7 7h7a3 3 0 010 6H7h8a3 3 0 010 6H7", "pricing"),
    ("staff-website-publish-queue", "Preview & publish", "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z", "publish"),
)

WEBSITE_SECTIONS = {
    "staff-website-pages": "pages", "staff-website-create": "pages", "staff-website-edit": "pages",
    "staff-website-preview": "pages", "staff-website-history": "pages", "staff-website-menu": "pages",
    "staff-website-home": "home", "staff-website-home-history": "home", "staff-website-home-preview": "home",
    "staff-website-images": "media", "staff-website-seo": "seo", "staff-website-pricing": "pricing",
    "staff-website-publish-queue": "publish",
}


def _website_nav(request, user, website_only: bool) -> dict:
    """The Website admin menu, for people who can edit the website."""
    from django.urls import reverse

    from apps.accounts.permissions import WEBSITE_EDIT, has_perm

    if not has_perm(user, WEBSITE_EDIT):
        return {}
    match = getattr(request, "resolver_match", None)
    section = WEBSITE_SECTIONS.get(match.url_name if match else "")
    if section is None and not website_only:
        return {}
    items = []
    for name, label, icon, key in WEBSITE_NAV:
        url_name, _, anchor = name.partition("#")
        items.append({"url": reverse(url_name) + (f"#{anchor}" if anchor else ""), "label": label, "icon": icon,
                      "current": key == section})
    return {"website_nav": items}


CLIENT_NAV = (
    ("client-overview", "Dashboard", "M3 12l9-9 9 9M5 10v10h14V10"),
    ("task-submit", "Submit a Task", "M12 5v14M5 12h14"),
    ("task-list", "My Tasks", "M4 6h16M4 12h16M4 18h10"),
    ("service-catalogue", "Service Catalogue", "M4 5h16v4H4zM4 15h16v4H4z"),
    ("files-home", "Deliverables & Files", "M14 3v5h5M6 3h9l4 4v14H6z"),
    ("messages-home", "Messages", "M21 15a4 4 0 01-4 4H8l-5 3V7a4 4 0 014-4h10a4 4 0 014 4z"),
    ("plan-home", "Plan & Usage", "M4 19h16M7 16V9M12 16V5M17 16v-4"),
    ("billing-home", "Billing & Invoices", "M3 6h18v12H3zM3 10h18"),
    ("team-home", "Organization & Team", "M16 11a4 4 0 10-8 0 4 4 0 008 0zM3 21a9 9 0 0118 0"),
    ("support-home", "Help & Support", "M12 18h.01M9.1 9a3 3 0 015.8 1c0 2-3 2-3 4"),
    ("security-home", "Profile & Security", "M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4z"),
)


def navigation(request):
    user = getattr(request, "user", None)
    context = {
        "client_nav": (),
        "staff_nav": (),
        "is_staff_member": False,
        "client_organization": None,
        "staff_role_label": "",
        "unread_notifications": 0,
        "nav_items": (),
        "counselling": None,
        "account_paused": False,
        "terms_to_confirm": False,
    }
    if user is None or not user.is_authenticated:
        return context

    from apps.communications.services import unread_count

    context["unread_notifications"] = unread_count(user)

    staff_role = getattr(user, "staff_role", "")
    if staff_role:
        from apps.accounts.models import StaffRole
        from apps.accounts.permissions import has_perm

        website_only = staff_role in (StaffRole.WEBSITE_EDITOR, StaffRole.WEBSITE_PUBLISHER) and not user.is_superuser
        allowed = []
        for name, label, icon in STAFF_NAV:
            if name == "staff-my-cases" and staff_role != StaffRole.SPECIALIST:
                continue
            if website_only and name in ("staff-dashboard", "staff-queue", "staff-website-pages"):
                continue  # their task views are always empty, and Website has its own menu below
            required = STAFF_NAV_PERMISSIONS.get(name)
            if required and not has_perm(user, required):
                continue
            allowed.append((name, label, icon))
        context.update(_website_nav(request, user, website_only))
        context["staff_nav"] = tuple(allowed)
        context["nav_items"] = context["staff_nav"]
        context["is_staff_member"] = True
        context["staff_role_label"] = StaffRole(staff_role).label
        return context

    from apps.organizations.access import memberships_for

    memberships = list(
        memberships_for(user).select_related("organization").order_by("organization__name")
    )
    if memberships:
        context["client_nav"] = CLIENT_NAV
        organization = request.session.get("organization_id")
        selected = next(
            (m.organization for m in memberships if str(m.organization_id) == organization),
            memberships[0].organization,
        )
        context["client_organization"] = selected
        from apps.billing.plan_rules import is_paused

        context["account_paused"] = is_paused(selected)
        from apps.accounts.profile import needs_terms_confirmation

        context["terms_to_confirm"] = needs_terms_confirmation(user)
    context["nav_items"] = context["client_nav"] or tuple(n for n in CLIENT_NAV if n[0] == "security-home")
    # The "Need Counselling?" button: clients only. Nothing records who opens it.
    from apps.communications.models import CounsellingSettings

    if getattr(user, "is_client_account", False):
        counselling = CounsellingSettings.current()
        context["counselling"] = counselling.as_widget_config() if counselling.enabled else None
    return context
