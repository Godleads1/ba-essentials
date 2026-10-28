from __future__ import annotations

from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.billing import services as billing
from apps.billing.models import Invoice, InvoiceStatus
from apps.core import ratelimit
from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error
from apps.organizations.access import client_required
from apps.quotes.models import QuoteStatus
from apps.reviews.models import DeliverableVersionStatus

from . import services
from .access import tasks_for
from .forms import MAX_ATTACHMENTS, SubmitTaskForm
from .models import OPEN_STATUSES, TaskStatus
from .transitions import Action, available_actions


def _task_for_org(request, task_id):
    task = (
        tasks_for(request.user)
        .filter(pk=task_id, organization=request.organization)
        .select_related(
            "organization",
            "service_version",
            "service_version__definition",
            "service_version__definition__category",
        )
        .first()
    )
    if task is None:
        raise Http404("Task not found")
    return task


@client_required
def overview(request):
    organization = request.organization
    tasks = tasks_for(request.user).filter(organization=organization)
    counts = tasks.aggregate(
        open_tasks=Count("id", filter=Q(status__in=OPEN_STATUSES)),
        awaiting_client=Count(
            "id",
            filter=Q(
                status__in=[
                    TaskStatus.NEEDS_INFORMATION,
                    TaskStatus.AWAITING_CLIENT_APPROVAL,
                    TaskStatus.DELIVERED,
                ]
            ),
        ),
        completed=Count("id", filter=Q(status=TaskStatus.COMPLETED)),
    )
    subscription = billing.active_subscription(organization)
    recent = list(tasks.select_related("service_version__definition")[:5])
    outstanding = Invoice.objects.filter(
        organization=organization, status=InvoiceStatus.ISSUED
    ).order_by("due_at")[:5]
    return render(
        request,
        "app/overview.html",
        {
            **counts,
            "recent_tasks": recent,
            "subscription": subscription,
            "allowance_remaining": billing.allowance_remaining(organization),
            "outstanding_invoices": outstanding,
        },
    )


@client_required
def task_list(request):
    tasks = (
        tasks_for(request.user)
        .filter(organization=request.organization)
        .select_related("service_version__definition")
    )
    status = request.GET.get("status", "")
    if status in TaskStatus.values:
        tasks = tasks.filter(status=status)
    query = (request.GET.get("q") or "").strip()
    if query:
        tasks = tasks.filter(
            Q(title__icontains=query)
            | Q(reference__icontains=query)
            | Q(brief__icontains=query)
        )
    return render(
        request,
        "app/tasks_list.html",
        {
            "tasks": tasks[:200],
            "statuses": TaskStatus.choices,
            "selected_status": status,
            "query": query,
        },
    )


@client_required
@require_http_methods(["GET", "POST"])
def task_submit(request):
    form = SubmitTaskForm(request.POST or None)
    if request.method == "POST":
        if not ratelimit.hit(
            "task-submit", str(request.organization.pk), limit=20, window_seconds=3600
        ):
            messages.error(request, "Too many requests submitted. Please try again later.")
        elif form.is_valid():
            files = request.FILES.getlist("attachments")[:MAX_ATTACHMENTS]
            try:
                task = services.create_request(
                    organization=request.organization,
                    user=request.user,
                    service_version=form.cleaned_data["service_version"],
                    title=form.cleaned_data["title"],
                    brief=form.cleaned_data["brief"],
                    desired_outcome=form.cleaned_data["desired_outcome"],
                    priority=form.cleaned_data["priority"],
                    due_date=form.cleaned_data["due_date"],
                    files=files,
                )
            except DomainError as exc:
                return handle_domain_error(request, exc, redirect_to="task-submit")
            messages.success(
                request, f"Request {task.reference} submitted. The team will triage it."
            )
            return redirect("task-detail", task_id=task.pk)
    return render(request, "app/task_submit.html", {"form": form})


@client_required
def task_detail(request, task_id):
    task = _task_for_org(request, task_id)
    events = task.status_events.select_related("actor")[:50]
    attachments = task.attachments.select_related("file").all()

    quote = getattr(task, "quote", None)
    quote_versions = (
        quote.versions.order_by("-version_number").all() if quote is not None else []
    )
    sent_quote = (
        quote.versions.filter(status=QuoteStatus.SENT).first() if quote is not None else None
    )

    deliverable = getattr(task, "deliverable", None)
    released_version = None
    if deliverable is not None:
        released_version = (
            deliverable.versions.filter(status=DeliverableVersionStatus.RELEASED)
            .select_related("file")
            .first()
        )

    invoice = (
        Invoice.objects.filter(task=task).order_by("-issued_at").first()
    )
    actions = available_actions(task, request.user)
    return render(
        request,
        "app/task_detail.html",
        {
            "task": task,
            "events": events,
            "attachments": attachments,
            "quote_versions": quote_versions,
            "sent_quote": sent_quote,
            "released_version": released_version,
            "invoice": invoice,
            "actions": actions,
            "can_provide_information": Action.PROVIDE_INFORMATION.value in actions,
            "can_cancel": Action.CANCEL_CLIENT.value in actions,
            "can_decide_deliverable": Action.CLIENT_ACCEPT.value in actions,
            "idempotency_key": f"accept-{sent_quote.pk if sent_quote else 'none'}-{request.user.pk}",
        },
    )


@client_required
@require_POST
def provide_information(request, task_id):
    task = _task_for_org(request, task_id)
    try:
        services.provide_information(
            task, request.user, request.POST.get("information", "")
        )
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to="task-detail")
    messages.success(request, "Thanks — the team has your information.")
    return redirect("task-detail", task_id=task.pk)


@client_required
@require_POST
def cancel_task(request, task_id):
    task = _task_for_org(request, task_id)
    try:
        services.cancel_by_client(task, request.user, request.POST.get("reason", ""))
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to="task-detail")
    messages.success(request, "Request cancelled.")
    return redirect("task-detail", task_id=task.pk)
