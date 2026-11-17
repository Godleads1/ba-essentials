from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core import client_status, ratelimit
from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error
from apps.organizations.access import client_required
from apps.tasks.access import tasks_for

from . import services
from .forms import SupportTicketForm
from .models import Notification, SupportTicket, TaskMessage


@client_required
@require_GET
def messages_home(request):
    """Messages & Notifications: one conversation per request, with the thread open beside the list."""
    from django.db.models import OuterRef, Q, Subquery

    visible = tasks_for(request.user).filter(organization=request.organization)
    public = TaskMessage.objects.filter(task=OuterRef("pk"), is_internal=False).order_by("-created_at")
    convos = (
        visible.filter(messages__is_internal=False)
        .annotate(last_message_at=Max("messages__created_at"),
                  last_body=Subquery(public.values("body")[:1]),
                  last_role=Subquery(public.values("author_role")[:1]))
        .select_related("service_version__definition__category")
        .order_by("-last_message_at")
    )
    waiting = client_status.statuses_in("input")
    query = (request.GET.get("q") or "").strip()[:100]
    show = request.GET.get("show", "")
    if query:
        convos = convos.filter(Q(title__icontains=query) | Q(reference__icontains=query))
    if show == "input":
        convos = convos.filter(status__in=waiting)
    else:
        show = ""
    convos = list(convos[:50])

    selected = None
    task_id = request.GET.get("task", "")
    if task_id:
        try:
            selected = visible.select_related("service_version__definition").filter(pk=uuid.UUID(task_id)).first()
        except ValueError:
            selected = None
        if selected is None:
            raise Http404("Task not found")
    elif convos:
        selected = convos[0]
    thread = []
    if selected is not None:
        thread = list(services.task_thread(selected, request.user))
        for m in thread:
            m.mine = m.author_id == request.user.pk

    updates = Notification.objects.filter(user=request.user).select_related("task", "ticket")[:5]
    return render(request, "banda/app/messages.html", {
        "convos": convos, "selected": selected, "thread": thread, "opened": bool(task_id),
        "query": query, "show": show, "filtered": bool(query or show),
        "updates": updates, "unread_notifications": services.unread_count(request.user),
        "waiting_statuses": waiting,
    })


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
    # Only a fixed value picks the page to return to, never a URL from the form.
    if request.POST.get("back") == "messages":
        back = f"{reverse('messages-home')}?task={task.pk}"
    else:
        back = f"{reverse('task-detail', args=[task.pk])}"
    if not ratelimit.hit(
        "task-message", str(request.user.pk), limit=60, window_seconds=3600
    ):
        messages.error(request, "You are sending messages too quickly. Try again later.")
        return redirect(back)
    try:
        services.post_task_message(
            task=task,
            author=request.user,
            body=request.POST.get("body", ""),
            internal=False,
        )
    except DomainError as exc:
        return handle_domain_error(
            request, exc, redirect_to=back
        )
    return redirect(f"{back}#{'latest' if request.POST.get('back') == 'messages' else 'messages'}")


@login_required
@require_GET
def notifications(request):
    items = Notification.objects.filter(user=request.user).select_related("task", "ticket")
    unread = services.unread_count(request.user)
    if not request.user.is_client_account:
        return render(request, "banda/staff/notifications.html",
                      {"notifications": items[:100], "unread_notifications": unread})
    show = request.GET.get("show", "")
    if show == "unread":
        items = items.filter(read_at__isnull=True)
    else:
        show = ""
    return render(request, "banda/app/notifications.html", {
        "notifications": items[:100], "unread_notifications": unread, "show": show,
    })


@login_required
@require_POST
def mark_all_read(request):
    updated = services.mark_notifications_read(request.user)
    if updated:
        messages.success(request, f"{updated} notification(s) marked as read.")
    return redirect("notifications")


TICKET_STATUS = client_status.TICKET_STATUS
TOPICS = {
    "tasks": ("Tasks & requests", "square-check"),
    "files": ("Files & deliverables", "folder"),
    "billing": ("Billing & invoices", "receipt-text"),
    "plan": ("Plan & usage", "gauge"),
    "account": ("Your account & sign-in", "user"),
    "privacy": ("Your data & privacy", "shield"),
    "technical": ("Something isn't working", "wrench"),
    "other": ("Something else", "circle-help"),
}


def _faq(max_mb: int, dpo_email: str) -> list[tuple[str, str]]:
    return [
        ("How do I sign in?", ("With a one-time code we send to your email, or by text or WhatsApp where that's "
         "offered. There's no password. We will never ask you for a code, a password or card details, so don't "
         "share a code with anyone, even someone who says they work for us.")),
        ("How do I submit a task?", ("Go to Submit a Task, pick a service, add the details and any files, then "
         "submit. You get a reference number and can follow the request in My Tasks.")),
        ("Where are my finished files?", ("In Deliverables & Files, and on the request's own page. Work appears "
         "there once our quality team has checked it.")),
        ("How do I ask for changes?", ("Open the delivered request. You can accept the work, or ask for changes "
         "and say what should change. The page shows how many rounds of changes are included.")),
        ("Which files can I upload?", (f"PDF, Word (.docx), Excel (.xlsx), CSV, text, JPG and PNG, up to {max_mb} MB "
         "each. Send only what the task needs, and never upload passwords or card details.")),
        ("Where can I find my invoices?", ("In Billing & Invoices. You can view and pay them there.")),
        ("How do I see, correct or delete my data?", ("Go to Privacy & data in Profile & security to download a copy "
         f"or make a request, or email our data protection contact at {dpo_email}.")),
        ("How do I close my account?", ("The business owner can close the account in Profile & security. Your "
         "plan stops renewing, you keep full access until the end of the period you've paid for, and then the "
         "account closes.")),
    ]


def _ticket_rows(tickets):
    return [{"ticket": t, "status": TICKET_STATUS.get(t.status, ("Open", TICKET_STATUS["open"][1]))} for t in tickets]


@client_required
@require_GET
def support_home(request):
    """Help & Support: ways to get help, the business's support requests and common questions."""
    from django.conf import settings

    tickets = SupportTicket.objects.filter(organization=request.organization)
    counts = {"all": tickets.count(), "active": tickets.filter(status__in=("open", "pending")).count()}
    show = request.GET.get("status", "")
    if show == "active":
        tickets = tickets.filter(status__in=("open", "pending"))
    elif show == "done":
        tickets = tickets.filter(status__in=("resolved", "closed"))
    else:
        show = ""
    return render(request, "banda/app/support_list.html", {
        "rows": _ticket_rows(tickets[:100]), "counts": counts, "show": show,
        "topics": [(k, label, icon) for k, (label, icon) in TOPICS.items()],
        "support_email": settings.SUPPORT_EMAIL,
        "faq": _faq(settings.FILE_MAX_BYTES // (1024 * 1024), settings.DPO_EMAIL),
    })


@client_required
@require_http_methods(["GET", "POST"])
def support_new(request):
    topic = TOPICS.get(request.GET.get("topic", ""))
    initial = {"subject": f"{topic[0]}: "} if topic else {}
    form = SupportTicketForm(request.POST or None, initial=initial)
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
    return render(request, "banda/app/support_new.html", {
        "form": form, "topics": [(k, label, icon) for k, (label, icon) in TOPICS.items()],
        "topic": request.GET.get("topic", "") if topic else "",
    })


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

    thread = list(services.ticket_thread(ticket, request.user))
    for m in thread:
        m.mine = m.author_id == request.user.pk
    return render(request, "banda/app/support_detail.html", {
        "ticket": ticket, "thread": thread,
        "status": TICKET_STATUS.get(ticket.status, TICKET_STATUS["open"]),
        "can_reply": ticket.status != "closed",
    })


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
