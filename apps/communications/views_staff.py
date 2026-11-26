from __future__ import annotations

import uuid

from django.contrib import messages
from django.db.models import Case, Count, F, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import staff_permission_required, staff_required
from apps.accounts.models import StaffRole, User
from apps.accounts.permissions import SUPPORT_HANDLE
from apps.core import ratelimit
from apps.core.exceptions import DomainError, ValidationFailure
from apps.core.views import handle_domain_error
from apps.operations import staff_access
from apps.tasks.access import can_view_task
from apps.tasks.models import Task

from . import services
from .models import SupportTicket, SupportTicketStatus


@staff_permission_required(SUPPORT_HANDLE)
@require_GET
def support_queue(request):
    tickets = SupportTicket.objects.select_related(
        "organization", "created_by", "assigned_to"
    )
    status = request.GET.get("status", "")
    if status in SupportTicketStatus.values:
        tickets = tickets.filter(status=status)
    elif status != "all":
        tickets = tickets.filter(
            status__in=[SupportTicketStatus.OPEN, SupportTicketStatus.PENDING]
        )
    if request.GET.get("mine") == "1":
        tickets = tickets.filter(assigned_to=request.user)

    counts = SupportTicket.objects.aggregate(
        open=Count("id", filter=Q(status=SupportTicketStatus.OPEN)),
        pending=Count("id", filter=Q(status=SupportTicketStatus.PENDING)),
        resolved=Count("id", filter=Q(status=SupportTicketStatus.RESOLVED)),
        closed=Count("id", filter=Q(status=SupportTicketStatus.CLOSED)),
        unassigned=Count(
            "id",
            filter=Q(
                status__in=[SupportTicketStatus.OPEN, SupportTicketStatus.PENDING],
                assigned_to__isnull=True,
            ),
        ),
        overdue=Count("id", filter=Q(
            status__in=[SupportTicketStatus.OPEN, SupportTicketStatus.PENDING],
            first_reply_at__isnull=True, reply_due_at__lt=timezone.now(),
        )),
    )
    # Waiting for a first reply comes first, earliest due first (higher plans have shorter
    # targets, so they rise on their own), then the higher plan.
    awaiting_first_reply = Case(
        When(first_reply_at__isnull=True, status=SupportTicketStatus.OPEN, then=Value(0)),
        default=Value(1), output_field=IntegerField(),
    )
    rows = list(tickets.order_by(
        awaiting_first_reply, F("reply_due_at").asc(nulls_last=True), "-plan_rank", "-last_message_at",
    )[:200])
    staff_access.log_list(request, "support", count=len(rows))
    return render(
        request,
        "banda/staff/support.html",
        {
            "tickets": rows,
            "counts": counts,
            "statuses": SupportTicketStatus.choices,
            "selected_status": status,
            "mine": request.GET.get("mine") == "1",
        },
    )


@staff_permission_required(SUPPORT_HANDLE)
@require_http_methods(["GET", "POST"])
def support_detail(request, ticket_id):
    ticket = (
        SupportTicket.objects.select_related(
            "organization", "created_by", "assigned_to"
        )
        .filter(pk=ticket_id)
        .first()
    )
    if ticket is None:
        raise Http404("Ticket not found")

    if request.method == "POST":
        action = request.POST.get("action", "reply")
        try:
            if action == "reply":
                services.post_ticket_message(
                    ticket=ticket,
                    author=request.user,
                    body=request.POST.get("body", ""),
                    internal=bool(request.POST.get("internal")),
                )
                messages.success(request, "Reply saved.")
            elif action == "assign":
                try:
                    assignee_id = uuid.UUID(str(request.POST.get("assignee") or ""))
                except (TypeError, ValueError):
                    raise ValidationFailure("Choose a support team member.")
                assignee = User.objects.filter(pk=assignee_id, is_active=True).first()
                if assignee is None:
                    raise ValidationFailure("Choose a support team member.")
                services.assign_ticket(ticket, request.user, assignee)
                messages.success(request, f"Assigned to {assignee.full_name or assignee.email}.")
            elif action == "status":
                services.set_ticket_status(
                    ticket, request.user, request.POST.get("status", "")
                )
                messages.success(request, "Ticket status updated.")
            else:
                raise ValidationFailure("Unknown ticket action.")
        except DomainError as exc:
            return handle_domain_error(
                request,
                exc,
                redirect_to=reverse("staff-support-detail", args=[ticket.pk]),
            )
        return redirect("staff-support-detail", ticket_id=ticket.pk)

    staff_access.log_list(request, "support_ticket", count=1, organization=ticket.organization)
    agents = User.objects.filter(
        staff_role__in=[StaffRole.SUPPORT, StaffRole.OPERATIONS, StaffRole.ADMIN],
        is_active=True,
    ).order_by("full_name")
    return render(
        request,
        "banda/staff/support_detail.html",
        {
            "ticket": ticket,
            "thread": services.ticket_thread(ticket, request.user),
            "agents": agents,
            "statuses": SupportTicketStatus.choices,
        },
    )


@staff_required
@require_POST
def post_task_message(request, task_id):
    task = Task.objects.filter(pk=task_id).first()
    if task is None or not can_view_task(request.user, task):
        raise Http404("Task not found")
    if not ratelimit.hit(
        "task-message", str(request.user.pk), limit=120, window_seconds=3600
    ):
        messages.error(request, "You are sending messages too quickly. Try again later.")
        return redirect("staff-task-detail", task_id=task.pk)
    try:
        services.post_task_message(
            task=task,
            author=request.user,
            body=request.POST.get("body", ""),
            internal=bool(request.POST.get("internal")),
        )
    except DomainError as exc:
        return handle_domain_error(
            request, exc, redirect_to=reverse("staff-task-detail", args=[task.pk])
        )
    return redirect("staff-task-detail", task_id=task.pk)
