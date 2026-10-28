from __future__ import annotations

from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import staff_permission_required, staff_required
from apps.accounts.models import StaffRole, User
from apps.accounts.permissions import (
    CLIENT_VIEW,
    QUOTE_SEND,
    REVIEW_DECIDE,
    has_perm,
)
from apps.billing.models import Invoice
from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error
from apps.organizations.models import Organization
from apps.quotes.forms import QuoteForm
from apps.reviews.models import DeliverableVersionStatus
from apps.tasks import services
from apps.tasks.access import can_view_task, tasks_for
from apps.tasks.models import (
    AssignmentStatus,
    OPEN_STATUSES,
    Task,
    TaskStatus,
    TaskStatusEvent,
)
from apps.tasks.transitions import Action, available_actions

from .forms import AssignForm, WorkLogForm

ACTIVE_GROUPS = {
    "new": [TaskStatus.SUBMITTED],
    "triage": [TaskStatus.TRIAGE, TaskStatus.NEEDS_INFORMATION],
    "scoping": [
        TaskStatus.SCOPING,
        TaskStatus.AWAITING_CLIENT_APPROVAL,
        TaskStatus.AWAITING_PAYMENT,
    ],
    "delivery": [
        TaskStatus.READY_FOR_ASSIGNMENT,
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.INTERNAL_REVIEW,
        TaskStatus.REWORK_REQUIRED,
        TaskStatus.APPROVED_FOR_RELEASE,
    ],
    "client": [TaskStatus.DELIVERED, TaskStatus.REVISION_REQUESTED],
}


def _task(request, task_id) -> Task:
    task = (
        Task.objects.filter(pk=task_id)
        .select_related(
            "organization",
            "created_by",
            "service_version__definition",
            "service_version__definition__category",
        )
        .first()
    )
    if task is None or not can_view_task(request.user, task):
        raise Http404("Task not found")
    return task


@staff_required
@require_GET
def dashboard(request):
    visible = tasks_for(request.user)
    counts = visible.aggregate(
        new=Count("id", filter=Q(status__in=ACTIVE_GROUPS["new"])),
        triage=Count("id", filter=Q(status__in=ACTIVE_GROUPS["triage"])),
        scoping=Count("id", filter=Q(status__in=ACTIVE_GROUPS["scoping"])),
        delivery=Count("id", filter=Q(status__in=ACTIVE_GROUPS["delivery"])),
        awaiting_client=Count("id", filter=Q(status__in=ACTIVE_GROUPS["client"])),
        open=Count("id", filter=Q(status__in=OPEN_STATUSES)),
    )
    events = (
        TaskStatusEvent.objects.filter(task__in=visible)
        .select_related("task", "actor")
        .order_by("-created_at")[:12]
    )
    return render(
        request,
        "staff/dashboard.html",
        {"counts": counts, "events": events, "groups": ACTIVE_GROUPS},
    )


@staff_required
@require_GET
def queue(request):
    tasks = tasks_for(request.user).select_related(
        "organization", "service_version__definition"
    )
    status = request.GET.get("status", "")
    if status in TaskStatus.values:
        tasks = tasks.filter(status=status)
    else:
        tasks = tasks.filter(status__in=OPEN_STATUSES)
    query = (request.GET.get("q") or "").strip()
    if query:
        tasks = tasks.filter(
            Q(reference__icontains=query)
            | Q(title__icontains=query)
            | Q(organization__name__icontains=query)
        )
    if request.GET.get("mine") == "1":
        tasks = tasks.filter(
            assignments__specialist=request.user,
            assignments__status=AssignmentStatus.ACTIVE,
        )
    return render(
        request,
        "staff/queue.html",
        {
            "tasks": tasks.distinct()[:200],
            "statuses": TaskStatus.choices,
            "selected_status": status,
            "query": query,
            "mine": request.GET.get("mine") == "1",
        },
    )


@staff_required
@require_GET
def task_detail(request, task_id):
    task = _task(request, task_id)
    actions = available_actions(task, request.user)
    assignment = task.active_assignment
    deliverable = getattr(task, "deliverable", None)
    versions = (
        deliverable.versions.select_related("file", "submitted_by", "reviewed_by").all()
        if deliverable is not None
        else []
    )
    quote = getattr(task, "quote", None)
    quote_versions = quote.versions.all() if quote is not None else []
    invoice = Invoice.objects.filter(task=task).order_by("-issued_at").first()
    pending_versions = [v for v in versions if v.status == DeliverableVersionStatus.PENDING_REVIEW]
    approved_version = next(
        (v for v in versions if v.status == DeliverableVersionStatus.APPROVED), None
    )
    can_quote = has_perm(request.user, QUOTE_SEND) and task.status == TaskStatus.SCOPING
    can_review = has_perm(request.user, REVIEW_DECIDE) and bool(pending_versions)
    return render(
        request,
        "staff/task_detail.html",
        {
            "task": task,
            "events": task.status_events.select_related("actor")[:50],
            "attachments": task.attachments.select_related("file").all(),
            "actions": actions,
            "assignment": assignment,
            "work_logs": assignment.work_logs.select_related("author")[:20] if assignment else [],
            "deliverable_versions": versions,
            "pending_versions": pending_versions,
            "approved_version": approved_version,
            "quote_versions": quote_versions,
            "quote_form": QuoteForm(),
            "assign_form": AssignForm(),
            "work_form": WorkLogForm(),
            "invoice": invoice,
            "specialists": User.objects.filter(
                staff_role=StaffRole.SPECIALIST, is_active=True
            ).order_by("full_name"),
            "Action": Action,
            "can_quote": can_quote,
            "can_review": can_review,
            "can_release": Action.RELEASE.value in actions,
            "is_active_specialist": bool(
                assignment and assignment.specialist_id == request.user.pk
            ),
        },
    )


def _run(request, task_id, func):
    task = _task(request, task_id)
    try:
        func(task)
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to="staff-task-detail")
    return redirect("staff-task-detail", task_id=task.pk)


@staff_required
@require_POST
def triage(request, task_id):
    from apps.tasks import transitions

    def action(task):
        transitions.transition(task=task, action=Action.TRIAGE, actor=request.user)
        messages.success(request, "Request moved into triage.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def request_information(request, task_id):
    def action(task):
        services.request_information(
            task, request.user, request.POST.get("question", "")
        )
        messages.info(request, "The client has been asked for more information.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def begin_scoping(request, task_id):
    def action(task):
        services.begin_scoping(task, request.user)
        messages.success(request, "Scoping started.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def assign(request, task_id):
    def action(task):
        specialist_id = request.POST.get("specialist", "")
        specialist = User.objects.filter(
            pk=specialist_id, staff_role=StaffRole.SPECIALIST, is_active=True
        ).first()
        if specialist is None:
            from apps.core.exceptions import ValidationFailure

            raise ValidationFailure("Choose an active specialist.")
        services.assign_task(
            task, request.user, specialist, note=request.POST.get("note", "")
        )
        messages.success(request, f"Assigned to {specialist.full_name or specialist.email}.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def accept_assignment(request, task_id):
    def action(task):
        services.accept_assignment(task, request.user)
        messages.success(request, "Assignment accepted. Work can begin.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def add_work_log(request, task_id):
    def action(task):
        minutes = request.POST.get("minutes") or None
        services.add_work_log(
            task,
            request.user,
            request.POST.get("note", ""),
            minutes=int(minutes) if minutes else None,
        )
        messages.success(request, "Work log added.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def submit_deliverable(request, task_id):
    def action(task):
        uploaded = request.FILES.get("file")
        if uploaded is None:
            from apps.core.exceptions import ValidationFailure

            raise ValidationFailure("Choose the deliverable file to submit.")
        version = services.submit_deliverable(
            task, request.user, uploaded, notes=request.POST.get("notes", "")
        )
        messages.success(request, f"Draft v{version.version_number} sent for review.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def resume_work(request, task_id):
    def action(task):
        services.resume_work(task, request.user)
        messages.success(request, "Work resumed on the rework.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def start_revision(request, task_id):
    def action(task):
        services.start_revision(task, request.user)
        messages.success(request, "Revision started.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def hold(request, task_id):
    def action(task):
        services.hold_task(task, request.user, request.POST.get("reason", ""))
        messages.info(request, "Request placed on hold.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def resume(request, task_id):
    def action(task):
        services.resume_task(task, request.user, request.POST.get("reason", ""))
        messages.success(request, "Request resumed.")

    return _run(request, task_id, action)


@staff_required
@require_POST
def cancel(request, task_id):
    def action(task):
        services.cancel_by_staff(task, request.user, request.POST.get("reason", ""))
        messages.info(request, "Request cancelled.")

    return _run(request, task_id, action)


@staff_permission_required(CLIENT_VIEW)
@require_GET
def clients(request):
    organizations = (
        Organization.objects.annotate(
            task_total=Count("tasks", distinct=True),
            open_total=Count("tasks", filter=Q(tasks__status__in=OPEN_STATUSES), distinct=True),
        )
        .order_by("name")[:200]
    )
    return render(request, "staff/clients.html", {"organizations": organizations})
