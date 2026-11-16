"""Every look a staff member takes at client data is recorded (guidelines 4.3, 4.9).

Opening a request, and any list or search that shows client data, writes an audit
event with the client organisation where there is one. Search words are not
stored, only whether a search was made: they can contain client names.
"""
from __future__ import annotations

from apps.audit import services as audit_service


def _is_staff(user) -> bool:
    return bool(getattr(user, "staff_role", "") or getattr(user, "is_superuser", False))


def log_task_opened(request, task, *, view: str = "full") -> None:
    if not _is_staff(request.user):
        return
    audit_service.record(
        action="staff.task_opened", actor=request.user, organization_id=task.organization_id,
        resource_type="task", resource_id=task.reference,
        metadata={"view": view, "role": request.user.staff_role or "superuser"},
    )


def log_list(request, page: str, *, count: int, organization=None, searched: bool = False) -> None:
    if not _is_staff(request.user):
        return
    audit_service.record(
        action="staff.list_viewed", actor=request.user, organization=organization,
        resource_type="page", resource_id=page,
        metadata={"rows": count, "searched": "yes" if searched else "no",
                  "role": request.user.staff_role or "superuser"},
    )
