from __future__ import annotations

from apps.accounts.models import StaffRole
from apps.core.exceptions import PermissionFailure

# Permission codes are stable strings so they can be referenced from templates
# and services without importing role names everywhere.
TASK_VIEW_ALL = "task.view_all"
TASK_TRIAGE = "task.triage"
TASK_SCOPE = "task.scope"
TASK_ASSIGN = "task.assign"
TASK_RELEASE = "task.release"
TASK_HOLD = "task.hold"
TASK_CANCEL = "task.cancel"
QUOTE_SEND = "quote.send"
REVIEW_DECIDE = "review.decide"
FINANCE_VIEW = "finance.view"
FINANCE_VERIFY_PAYMENT = "finance.verify_payment"
CLIENT_VIEW = "client.view"
SUPPORT_HANDLE = "support.handle"
CATALOGUE_MANAGE = "catalogue.manage"
AUDIT_VIEW = "audit.view"
FILES_VIEW_ALL = "files.view_all"
WEBSITE_EDIT = "website.edit"
WEBSITE_PUBLISH = "website.publish"
LEADS_VIEW = "leads.view"

ALL_PERMISSIONS = frozenset(
    {
        TASK_VIEW_ALL,
        TASK_TRIAGE,
        TASK_SCOPE,
        TASK_ASSIGN,
        TASK_RELEASE,
        TASK_HOLD,
        TASK_CANCEL,
        QUOTE_SEND,
        REVIEW_DECIDE,
        FINANCE_VIEW,
        FINANCE_VERIFY_PAYMENT,
        CLIENT_VIEW,
        SUPPORT_HANDLE,
        CATALOGUE_MANAGE,
        AUDIT_VIEW,
        FILES_VIEW_ALL,
        WEBSITE_EDIT,
        WEBSITE_PUBLISH,
        LEADS_VIEW,
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    StaffRole.OPERATIONS: frozenset(
        {
            TASK_VIEW_ALL,
            TASK_TRIAGE,
            TASK_SCOPE,
            TASK_ASSIGN,
            TASK_RELEASE,
            TASK_HOLD,
            TASK_CANCEL,
            CLIENT_VIEW,
            SUPPORT_HANDLE,
            FILES_VIEW_ALL,
            LEADS_VIEW,
        }
    ),
    StaffRole.QUOTE_APPROVER: frozenset(
        {TASK_VIEW_ALL, TASK_SCOPE, QUOTE_SEND, CLIENT_VIEW}
    ),
    # Specialists only ever see their own assigned case; that is enforced by
    # assignment lookups, not by a broad "view all" permission.
    StaffRole.SPECIALIST: frozenset(),
    StaffRole.REVIEWER: frozenset({TASK_VIEW_ALL, REVIEW_DECIDE, FILES_VIEW_ALL}),
    StaffRole.SUPPORT: frozenset({TASK_VIEW_ALL, CLIENT_VIEW, SUPPORT_HANDLE}),
    StaffRole.FINANCE: frozenset(
        {FINANCE_VIEW, FINANCE_VERIFY_PAYMENT, CLIENT_VIEW, TASK_VIEW_ALL}
    ),
    StaffRole.ADMIN: ALL_PERMISSIONS,
    StaffRole.WEBSITE_EDITOR: frozenset({WEBSITE_EDIT}),
    StaffRole.WEBSITE_PUBLISHER: frozenset({WEBSITE_EDIT, WEBSITE_PUBLISH}),
}


def has_perm(user, code: str) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_active", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    role = getattr(user, "staff_role", "")
    if not role:
        return False
    return code in ROLE_PERMISSIONS.get(role, frozenset())


def require_perm(user, code: str) -> None:
    if not has_perm(user, code):
        raise PermissionFailure("You do not have permission to perform this action.")


def is_staff(user) -> bool:
    return getattr(user, "is_authenticated", False) and bool(getattr(user, "staff_role", ""))
