from __future__ import annotations

from apps.accounts.permissions import TASK_VIEW_ALL, has_perm
from apps.accounts.models import StaffRole
from apps.core.exceptions import ObjectNotFound, PermissionFailure
from apps.organizations.access import membership_for

from .models import AssignmentStatus, Task


def staff_can_view_all(user) -> bool:
    return has_perm(user, TASK_VIEW_ALL)


def can_view_task(user, task: Task) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "staff_role", ""):
        if staff_can_view_all(user):
            return True
        if user.staff_role == StaffRole.SPECIALIST:
            return task.assignments.filter(
                status=AssignmentStatus.ACTIVE, specialist=user
            ).exists()
        return False
    return membership_for(user, task.organization_id) is not None


def require_task_view(user, task: Task) -> None:
    if not can_view_task(user, task):
        raise PermissionFailure("You cannot access that request.")


def tasks_for(user):
    """Return the task queryset an actor may ever see. Filters tenant scope."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Task.objects.none()
    if getattr(user, "staff_role", ""):
        if staff_can_view_all(user):
            return Task.objects.all()
        if user.staff_role == StaffRole.SPECIALIST:
            return Task.objects.filter(
                assignments__specialist=user,
                assignments__status=AssignmentStatus.ACTIVE,
            ).distinct()
        return Task.objects.none()
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
