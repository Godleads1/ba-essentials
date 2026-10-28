from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error
from apps.organizations.access import client_required
from apps.tasks.access import tasks_for

from . import services
from .models import ClientDecision, DeliverableVersion, DeliverableVersionStatus


def _client_task(request, task_id):
    task = (
        tasks_for(request.user)
        .filter(pk=task_id, organization=request.organization)
        .first()
    )
    if task is None:
        raise Http404("Task not found")
    return task


@client_required
@require_POST
def decide(request, task_id):
    task = _client_task(request, task_id)
    version = (
        DeliverableVersion.objects.filter(
            pk=request.POST.get("version_id"),
            deliverable__task=task,
            status=DeliverableVersionStatus.RELEASED,
        )
        .select_related("deliverable__task")
        .first()
    )
    if version is None:
        raise Http404("Deliverable not found")
    try:
        services.client_decide(
            task,
            version,
            request.user,
            decision=request.POST.get("decision", ""),
            note=request.POST.get("note", ""),
        )
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to="task-detail")
    if request.POST.get("decision") == ClientDecision.ACCEPT:
        messages.success(request, "Deliverable accepted. The request is complete.")
    else:
        messages.info(request, "Revision requested. The team will pick it up.")
    return redirect("task-detail", task_id=task.pk)
