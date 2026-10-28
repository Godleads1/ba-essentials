from __future__ import annotations

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error
from apps.organizations.access import client_required

from . import services
from .models import QuoteVersion


def _version_for_org(request, version_id) -> QuoteVersion:
    version = (
        QuoteVersion.objects.filter(
            pk=version_id, quote__task__organization=request.organization
        )
        .select_related("quote__task")
        .first()
    )
    if version is None:
        raise Http404("Quote not found")
    return version


@client_required
@require_POST
def accept_quote(request, version_id):
    version = _version_for_org(request, version_id)
    task = version.quote.task
    key = (request.POST.get("idempotency_key") or "").strip()[:128]
    if not key:
        key = f"accept-{version.pk}-{request.user.pk}"
    try:
        outcome = services.accept_quote(
            task=task, version=version, actor=request.user, idempotency_key=key
        )
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to="task-detail")
    if outcome.replayed:
        messages.info(request, "This quote was already accepted.")
    elif outcome.covered_by_allowance:
        messages.success(request, "Quote accepted. Your plan covers this request.")
    else:
        messages.success(
            request, "Quote accepted. Settle the invoice so the team can start work."
        )
    return redirect("task-detail", task_id=task.pk)


@client_required
@require_POST
def reject_quote(request, version_id):
    version = _version_for_org(request, version_id)
    task = version.quote.task
    try:
        services.reject_quote(
            task=task,
            version=version,
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to="task-detail")
    messages.info(request, "Quote declined. The team will follow up with options.")
    return redirect("task-detail", task_id=task.pk)
