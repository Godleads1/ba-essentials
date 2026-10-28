from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.accounts.decorators import staff_permission_required
from apps.accounts.permissions import QUOTE_SEND
from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error
from apps.tasks.models import Task

from . import services
from .models import QuoteVersion


def _staff_task(task_id) -> Task:
    task = Task.objects.select_related("organization").filter(pk=task_id).first()
    if task is None:
        raise Http404("Task not found")
    return task


@staff_permission_required(QUOTE_SEND)
@require_POST
def create_quote(request, task_id):
    from .forms import QuoteForm

    task = _staff_task(task_id)
    form = QuoteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Check the quote details and try again.")
        return redirect("staff-task-detail", task_id=task.pk)
    try:
        version = services.create_version(
            task,
            request.user,
            scope_summary=form.cleaned_data["scope_summary"],
            exclusions=form.cleaned_data["exclusions"],
            amount_minor=form.amount_minor,
            turnaround_days=form.cleaned_data["turnaround_days"],
            revision_allowance=form.cleaned_data["revision_allowance"],
            payment_terms=form.cleaned_data["payment_terms"],
        )
    except DomainError as exc:
        return handle_domain_error(
            request, exc, redirect_to="staff-task-detail"
        )
    messages.success(request, f"Quote v{version.version_number} saved as a draft.")
    return redirect("staff-task-detail", task_id=task.pk)


@staff_permission_required(QUOTE_SEND)
@require_POST
def send_quote(request, task_id, version_id):
    task = _staff_task(task_id)
    version = QuoteVersion.objects.filter(pk=version_id, quote__task=task).first()
    if version is None:
        raise Http404("Quote not found")
    try:
        services.send_version(task, version, request.user)
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to="staff-task-detail")
    messages.success(
        request, f"Quote v{version.version_number} sent to the client for approval."
    )
    return redirect("staff-task-detail", task_id=task.pk)
