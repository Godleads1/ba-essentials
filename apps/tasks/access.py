from __future__ import annotations

from django.db.models import Q

from apps.accounts.models import StaffRole
from apps.accounts.permissions import (
    FEES_APPROVE,
    FEES_SETTLE,
    FINANCE_VIEW,
    TASK_VIEW_ALL,
    has_perm,
)
from apps.core.exceptions import ObjectNotFound, PermissionFailure
from apps.organizations.access import membership_for

from .models import AssignmentStatus, Task, TaskStatus

# Need to know (AI_DEVELOPMENT_GUIDELINES 4.3). Who on the team sees a client's
# request, its messages and its files:
#   Operations, quote approvers, admins: every request (opening one is logged).
#   Specialists: their active assignments only.
#   Reviewers: requests in review, or with a version waiting for review.
#   Support: requests from businesses whose open tickets they're handling.
#   Finance: not the request's contents; only its money (see can_view_task_money).
REVIEW_STATUSES = (TaskStatus.INTERNAL_REVIEW, TaskStatus.REWORK_REQUIRED, TaskStatus.APPROVED_FOR_RELEASE)


def staff_can_view_all(user) -> bool:
    return has_perm(user, TASK_VIEW_ALL)


def _staff_scope(user) -> Q | None:
    """The tasks a staff member may open, as a filter. None means none at all."""
    if staff_can_view_all(user):
        return Q()
    role = user.staff_role
    if role == StaffRole.SPECIALIST:
        return Q(assignments__specialist=user, assignments__status=AssignmentStatus.ACTIVE)
    if role == StaffRole.REVIEWER:
        from apps.reviews.models import DeliverableVersionStatus

        return Q(status__in=REVIEW_STATUSES) | Q(
            deliverable__versions__status=DeliverableVersionStatus.PENDING_REVIEW
        )
    if role == StaffRole.SUPPORT:
        from apps.communications.models import SupportTicket, SupportTicketStatus

        orgs = SupportTicket.objects.filter(
            assigned_to=user, status__in=(SupportTicketStatus.OPEN, SupportTicketStatus.PENDING)
        ).values("organization_id")
        return Q(organization_id__in=orgs)
    return None


def can_view_task(user, task: Task) -> bool:
    """May this person see the request itself: brief, messages and files."""
    if user is None or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return False
    if getattr(user, "staff_role", "") or getattr(user, "is_superuser", False):
        scope = _staff_scope(user)
        if scope is None:
            return False
        return Task.objects.filter(scope, pk=task.pk).exists()
    return membership_for(user, task.organization_id) is not None


def can_view_task_money(user, task: Task) -> bool:
    """Finance sees a request's invoices and third-party fees, without its contents."""
    if can_view_task(user, task):
        return True
    if not any(has_perm(user, p) for p in (FINANCE_VIEW, FEES_APPROVE, FEES_SETTLE)):
        return False
    from apps.billing.models import FeeRequest, Invoice

    return (Invoice.objects.filter(task=task).exists()
            or FeeRequest.objects.filter(task=task).exists())


def require_task_view(user, task: Task) -> None:
    if not can_view_task(user, task):
        raise PermissionFailure("You cannot access that request.")


def tasks_for(user):
    """Return the task queryset an actor may ever see. Filters tenant scope."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Task.objects.none()
    if getattr(user, "staff_role", "") or getattr(user, "is_superuser", False):
        scope = _staff_scope(user) if getattr(user, "is_active", False) else None
        if scope is None:
            return Task.objects.none()
        if not scope:
            return Task.objects.all()
        return Task.objects.filter(pk__in=Task.objects.filter(scope).values("pk"))
    organization_ids = membership_for_org_ids(user)
    return Task.objects.filter(organization_id__in=organization_ids).distinct()


def membership_for_org_ids(user) -> list:
    from apps.organizations.access import memberships_for

    return list(
        memberships_for(user).values_list("organization_id", flat=True)
    )


def get_task_for_view(user, task_id) -> Task:
    task = tasks_for(user).select_related(
        "organization", "service_version", "service_version__definition"
    ).filter(pk=task_id).first()
    if task is None:
        raise ObjectNotFound("That request was not found.")
    return task
