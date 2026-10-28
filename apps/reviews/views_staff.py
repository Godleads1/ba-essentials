from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import staff_permission_required
from apps.accounts.permissions import REVIEW_DECIDE, TASK_RELEASE
from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error
from apps.tasks.access import can_view_task
from apps.tasks.models import Task

from . import services
from .models import (
    DeliverableVersion,
    DeliverableVersionStatus,
    ReviewDecision,
)


@staff_permission_required(REVIEW_DECIDE)
@require_GET
def review_queue(request):
    versions = (
        DeliverableVersion.objects.filter(status=DeliverableVersionStatus.PENDING_REVIEW)
        .select_related(
            "deliverable__task",
            "deliverable__task__organization",
            "deliverable__task__service_version__definition",
            "submitted_by",
            "file",
        )
        .order_by("created_at")
    )
    return render(
        request,
        "staff/reviews.html",
        {"versions": versions, "decisions": ReviewDecision.choices},
    )


@staff_permission_required(REVIEW_DECIDE)
@require_POST
def decide(request, task_id):
    task = _staff_task(request, task_id)
    version = DeliverableVersion.objects.filter(
        pk=request.POST.get("version_id"), deliverable__task=task
    ).first()
    if version is None:
        raise Http404("Deliverable not found")
    try:
        services.decide(
            task,
            version,
            request.user,
            decision=request.POST.get("decision", ""),
            findings=request.POST.get("findings", ""),
        )
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to="staff-reviews")
    messages.success(request, "Review recorded.")
    return redirect("staff-reviews")


@staff_permission_required(TASK_RELEASE)
@require_POST
def release(request, task_id):
    task = _staff_task(request, task_id)
    version = DeliverableVersion.objects.filter(
        pk=request.POST.get("version_id"), deliverable__task=task
    ).first()
    if version is None:
        raise Http404("Deliverable not found")
    try:
        services.release(task, version, request.user)
    except DomainError as exc:
        return handle_domain_error(
            request, exc, redirect_to="staff-task-detail"
        )
    messages.success(request, "Deliverable released to the client.")
    return redirect("staff-task-detail", task_id=task.pk)


def _staff_task(request, task_id) -> Task:
    task = (
        Task.objects.filter(pk=task_id)
        .select_related("organization", "service_version__definition")
        .first()
    )
    if task is None or not can_view_task(request.user, task):
        raise Http404("Task not found")
    return task
