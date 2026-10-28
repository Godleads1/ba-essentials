from __future__ import annotations

STAFF_NAV = (
    ("staff-dashboard", "Dashboard", "M3 12l9-9 9 9M5 10v10h14V10"),
    ("staff-queue", "Operations Queue", "M4 6h16M4 12h16M4 18h10"),
    ("staff-reviews", "Review Queue", "M9 12l2 2 4-4M12 3l9 4v5c0 5-4 8-9 9-5-1-9-4-9-9V7l9-4z"),
    ("staff-support", "Support", "M21 15a4 4 0 01-4 4H8l-5 3V7a4 4 0 014-4h10a4 4 0 014 4z"),
    ("staff-clients", "Clients", "M16 11a4 4 0 10-8 0 4 4 0 008 0zM3 21a9 9 0 0118 0"),
    ("staff-finance", "Finance", "M12 3v18M7 7h7a3 3 0 010 6H7h8a3 3 0 010 6H7"),
    ("staff-catalogue", "Service Catalogue", "M4 5h16v4H4zM4 15h16v4H4z"),
)

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
    }
    if user is None or not user.is_authenticated:
        return context

    from apps.communications.services import unread_count

    context["unread_notifications"] = unread_count(user)

    staff_role = getattr(user, "staff_role", "")
    if staff_role:
        from apps.accounts.permissions import has_perm
        from apps.accounts.models import StaffRole

        allowed = []
        for name, label, icon in STAFF_NAV:
            if name in {
                "staff-finance",
                "staff-catalogue",
                "staff-reviews",
                "staff-support",
            }:
                code = {
                    "staff-finance": "finance.view",
                    "staff-catalogue": "catalogue.manage",
                    "staff-reviews": "review.decide",
                    "staff-support": "support.handle",
                }[name]
                if not has_perm(user, code):
                    continue
            allowed.append((name, label, icon))
        context["staff_nav"] = tuple(allowed)
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
    return context
