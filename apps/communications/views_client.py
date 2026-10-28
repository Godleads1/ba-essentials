from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core import ratelimit
from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error
from apps.organizations.access import client_required
from apps.tasks.access import tasks_for

from . import services
from .forms import SupportTicketForm
from .models import Notification, SupportTicket


@client_required
@require_GET
def messages_home(request):
    tasks = (
        tasks_for(request.user)
        .filter(organization=request.organization, messages__is_internal=False)
        .annotate(last_message_at=Max("messages__created_at"))
        .select_related("service_version__definition")
        .order_by("-last_message_at")[:50]
    )
    return render(
        request,
        "app/messages.html",
        {
            "tasks": tasks,
            "unread_notifications": services.unread_count(request.user),
        },
    )


@client_required
@require_POST
def post_task_message(request, task_id):
    task = (
        tasks_for(request.user)
        .filter(pk=task_id, organization=request.organization)
        .first()
    )
    if task is None:
        raise Http404("Task not found")
    if not ratelimit.hit(
        "task-message", str(request.user.pk), limit=60, window_seconds=3600
    ):
        messages.error(request, "You are sending messages too quickly. Try again later.")
        return redirect("task-detail", task_id=task.pk)
    try:
        services.post_task_message(
            task=task,
            author=request.user,
            body=request.POST.get("body", ""),
            internal=False,
        )
    except DomainError as exc:
        return handle_domain_error(
            request, exc, redirect_to=reverse("task-detail", args=[task.pk])
        )
    return redirect(f"{reverse('task-detail', args=[task.pk])}#messages")


@login_required
@require_GET
def notifications(request):
    items = Notification.objects.filter(user=request.user).select_related(
        "task", "ticket"
    )[:100]
    return render(
        request,
        "app/notifications.html",
        {"notifications": items, "unread_notifications": services.unread_count(request.user)},
    )


@login_required
@require_POST
def mark_all_read(request):
    updated = services.mark_notifications_read(request.user)
    if updated:
        messages.success(request, f"{updated} notification(s) marked as read.")
    return redirect("notifications")


@client_required
@require_GET
def support_home(request):
    tickets = SupportTicket.objects.filter(
        organization=request.organization
    ).select_related("assigned_to")[:100]
    return render(request, "app/support_list.html", {"tickets": tickets})


@client_required
@require_http_methods(["GET", "POST"])
def support_new(request):
    form = SupportTicketForm(request.POST or None)
    if request.method == "POST":
        if not ratelimit.hit(
            "support-open", str(request.user.pk), limit=10, window_seconds=3600
        ):
            messages.error(request, "Too many tickets opened. Try again later.")
        elif form.is_valid():
            try:
                ticket = services.create_ticket(
                    organization=request.organization,
                    author=request.user,
                    subject=form.cleaned_data["subject"],
                    body=form.cleaned_data["body"],
                    priority=form.cleaned_data["priority"],
                )
            except DomainError as exc:
                return handle_domain_error(request, exc, redirect_to="support-new")
            messages.success(request, f"Ticket {ticket.reference} opened.")
            return redirect("support-detail", ticket_id=ticket.pk)
    return render(request, "app/support_new.html", {"form": form})


@client_required
@require_http_methods(["GET", "POST"])
def support_detail(request, ticket_id):
    ticket = SupportTicket.objects.filter(
        pk=ticket_id, organization=request.organization
    ).select_related("created_by", "assigned_to").first()
    if ticket is None:
        raise Http404("Ticket not found")

    if request.method == "POST":
        if not ratelimit.hit(
            "support-reply", str(request.user.pk), limit=60, window_seconds=3600
        ):
            messages.error(request, "You are replying too quickly. Try again later.")
        else:
            try:
                services.post_ticket_message(
                    ticket=ticket,
                    author=request.user,
                    body=request.POST.get("body", ""),
                    internal=False,
                )
            except DomainError as exc:
                return handle_domain_error(
                    request, exc, redirect_to=reverse("support-detail", args=[ticket.pk])
                )
        return redirect("support-detail", ticket_id=ticket.pk)

    return render(
        request,
        "app/support_detail.html",
        {
            "ticket": ticket,
            "thread": services.ticket_thread(ticket, request.user),
        },
    )


@client_required
@require_POST
def close_ticket(request, ticket_id):
    ticket = SupportTicket.objects.filter(
        pk=ticket_id, organization=request.organization
    ).first()
    if ticket is None:
        raise Http404("Ticket not found")
    try:
        services.close_ticket_by_client(ticket, request.user)
    except DomainError as exc:
        return handle_domain_error(
            request, exc, redirect_to=reverse("support-detail", args=[ticket.pk])
        )
    messages.success(request, "Ticket closed.")
    return redirect("support-detail", ticket_id=ticket.pk)
