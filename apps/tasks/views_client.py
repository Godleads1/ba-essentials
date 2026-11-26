from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.billing import services as billing
from apps.billing.models import (
    CLIENT_VISIBLE_FEE_STATUSES,
    FeeRequest,
    Invoice,
    InvoiceStatus,
)
from apps.billing.plan_rules import outside_plan_region as plan_rules_outside
from apps.communications import services as communications
from apps.core import ratelimit
from apps.core.countries import name_for as country_name
from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error
from apps.organizations.access import client_required
from apps.quotes.models import QuoteStatus
from apps.reviews.models import DeliverableVersionStatus

from . import services
from .access import tasks_for
from .forms import MAX_ATTACHMENTS, SubmitTaskForm
from .models import TaskStatus
from .transitions import Action, available_actions


def _is_uuid(value) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _mark_mine(messages, user):
    rows = list(messages)
    for m in rows:
        m.mine = m.author_id == user.pk
    return rows


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
    from django.utils import timezone

    from apps.organizations import account_manager

    from . import dashboard

    organization = request.organization
    tasks = tasks_for(request.user).filter(organization=organization)
    now = timezone.now()
    subscription = billing.active_subscription(organization)
    outstanding = Invoice.objects.filter(organization=organization, status=InvoiceStatus.ISSUED).order_by("due_at")
    return render(
        request,
        "banda/app/overview.html",
        {
            "greeting": dashboard.greeting(now),
            "first_name": (request.user.full_name or "").split(" ")[0],
            "stats": dashboard.stats(tasks, now),
            "week": dashboard.week(tasks, now),
            "waiting": dashboard.waiting_on_you(tasks),
            "deadlines": dashboard.deadlines(tasks),
            "recent_tasks": list(tasks.select_related("service_version__definition__category").order_by("-created_at")[:5]),
            "subscription": subscription,
            "usage": dashboard.plan_usage(organization, subscription),
            "outstanding_count": outstanding.count(),
            "first_outstanding": outstanding.first(),
            "account_manager": account_manager.manager_for(organization),
            "manager_promised": bool(subscription and subscription.plan.rank >= account_manager.DEDICATED_RANK),
        },
    )


@client_required
def task_list(request):
    """My tasks: search and filter on the server, 10 to a page."""
    from datetime import timedelta

    from django.core.paginator import Paginator
    from django.utils import timezone

    from apps.catalogue.models import ServiceCategory
    from apps.core import client_status as cs

    all_tasks = tasks_for(request.user).filter(organization=request.organization)
    tasks = all_tasks.select_related("service_version__definition__category").order_by("-created_at")
    group_choices = [(k, v[0]) for k, v in cs.GROUPS.items()]
    group = request.GET.get("group", "")
    if group == "active":
        tasks = tasks.filter(status__in=cs.statuses_in("progress", "checking"))
    elif group in cs.GROUPS:
        tasks = tasks.filter(status__in=cs.statuses_in(group))
    else:
        group = ""
    status = request.GET.get("status", "")
    if status in TaskStatus.values:
        tasks = tasks.filter(status=status)
    categories = list(ServiceCategory.objects.filter(
        services__versions__tasks__organization=request.organization).distinct().order_by("name"))
    tones = {c.pk: cs.CATEGORY_TONES[i % len(cs.CATEGORY_TONES)] for i, c in enumerate(categories)}
    category = request.GET.get("category", "")
    if category:
        tasks = tasks.filter(service_version__definition__category__slug=category)
    days = request.GET.get("days", "")
    if days in ("7", "30", "90"):
        tasks = tasks.filter(created_at__gte=timezone.now() - timedelta(days=int(days)))
    else:
        days = ""
    query = (request.GET.get("q") or "").strip()[:100]
    if query:
        tasks = tasks.filter(
            Q(title__icontains=query)
            | Q(reference__icontains=query)
            | Q(service_version__definition__name__icontains=query)
        )
    page = Paginator(tasks, 10).get_page(request.GET.get("page"))
    rows = [
        {"task": t, "tone": tones.get(t.service_version.definition.category_id, cs.CATEGORY_TONES[0]),
         "priority": cs.PRIORITY.get(t.priority, cs.PRIORITY["medium"]),
         "late": bool(t.due_date and t.due_date < timezone.localdate()
                      and t.status not in cs.statuses_in("delivered", "cancelled"))}
        for t in page.object_list
    ]
    filtered = bool(query or group or category or days or status)
    keep = request.GET.copy()
    keep.pop("page", None)
    return render(
        request,
        "banda/app/tasks_list.html",
        {
            "rows": rows,
            "page": page,
            "keep": keep.urlencode(),
            "counts": {
                "all": all_tasks.count(),
                "active": all_tasks.filter(status__in=cs.statuses_in("progress", "checking")).count(),
                "input": all_tasks.filter(status__in=cs.statuses_in("input")).count(),
                "delivered": all_tasks.filter(status__in=cs.statuses_in("delivered")).count(),
            },
            "group_choices": group_choices,
            "categories": categories,
            "guide": cs.guide(),
            "group": group, "category": category, "days": days, "query": query,
            "filtered": filtered,
        },
    )


@client_required
@require_http_methods(["GET", "POST"])
def task_submit(request):
    initial = {}
    service_id = request.GET.get("service", "").strip()
    if service_id:
        initial["service_version"] = service_id
    form = SubmitTaskForm(request.POST or None, initial=initial, organization=request.organization)
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
                    work_country=form.cleaned_data["work_country"],
                )
            except DomainError as exc:
                return handle_domain_error(request, exc, redirect_to="task-submit")
            messages.success(
                request, f"Request {task.reference} submitted. The team will triage it."
            )
            return redirect("task-detail", task_id=task.pk)
    from apps.billing import plan_rules
    from apps.catalogue.models import ServiceCategory
    from apps.core import client_status as cs

    # Category cards: open ones link to ?category=<slug>, which narrows the service
    # list on the server (no JavaScript needed). Locked ones say which plan has them.
    chosen_version = form.fields["service_version"].queryset.filter(pk=form["service_version"].value() or None).first() \
        if _is_uuid(form["service_version"].value()) else None
    category_slug = request.GET.get("category", "") or (chosen_version.definition.category.slug if chosen_version else "")
    cards = []
    for i, category in enumerate(ServiceCategory.objects.filter(is_active=True, services__is_active=True).distinct()):
        access = plan_rules.category_access(request.organization, category)
        cards.append({"category": category, "access": access, "selected": category.slug == category_slug,
                      "tone": cs.CATEGORY_TONES[i % len(cs.CATEGORY_TONES)]})
    selected = next((c for c in cards if c["selected"] and c["access"].allowed), None)
    if selected is not None:
        form.fields["service_version"].queryset = form.fields["service_version"].queryset.filter(
            definition__category=selected["category"])
    # What each service would use from the plan, shown before sending (Points rules).
    plan_balances = plan_rules.balances(request.organization)
    previews = {str(v.pk): plan_rules.preview(request.organization, v, plan_balances=plan_balances)["text"]
                for v in form.fields["service_version"].queryset.select_related("definition__category")}
    return render(request, "banda/app/task_submit.html", {
        "form": form, "cards": cards, "selected": selected, "chosen_version": chosen_version,
        "plan_balances": plan_balances, "previews": previews,
        "chosen_preview": previews.get(str(chosen_version.pk)) if chosen_version else "",
        "any_locked": any(not c["access"].allowed for c in cards),
        "paused": _is_paused(request.organization),
        "priority_value": form["priority"].value() or "medium",
    })


def _is_paused(organization):
    from apps.billing.plan_rules import is_paused

    return is_paused(organization)


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
    quote_plan = None
    if sent_quote is not None and sent_quote.amount_minor > 0:
        from apps.billing import plan_rules

        if not plan_rules.outside_plan_region(task):
            quote_plan = plan_rules.preview(request.organization, task.service_version,
                                            plan_rules.units_for(task.service_version, sent_quote))

    deliverable = getattr(task, "deliverable", None)
    released_version = None
    if deliverable is not None:
        released_version = (
            deliverable.versions.filter(status=DeliverableVersionStatus.RELEASED)
            .select_related("file")
            .first()
        )

    invoice = (
        Invoice.objects.filter(task=task, fee_request__isnull=True).order_by("-issued_at").first()
    )
    fee_requests = list(
        FeeRequest.objects.filter(task=task, status__in=CLIENT_VISIBLE_FEE_STATUSES)
        .select_related("invoice")
        .prefetch_related("lines__evidence", "lines__receipt")
    )
    actions = available_actions(task, request.user)
    from apps.core import client_status as cs

    from .dashboard import timeline

    released_versions = (
        list(deliverable.versions.filter(status=DeliverableVersionStatus.RELEASED).select_related("file")
             .order_by("-version_number"))
        if deliverable is not None else []
    )
    return render(
        request,
        "banda/app/task_detail.html",
        {
            "task": task,
            "timeline": timeline(task, list(events)),
            "released_versions": released_versions,
            "priority": cs.PRIORITY.get(task.priority, cs.PRIORITY["medium"]),
            "is_cancelled": task.status == "cancelled",
            "events": events,
            "attachments": attachments,
            "quote_versions": quote_versions,
            "sent_quote": sent_quote,
            "quote_plan": quote_plan,
            "released_version": released_version,
            "invoice": invoice,
            "fee_requests": fee_requests,
            "outside_plan_region": plan_rules_outside(task),
            "service_paused": _is_paused(task.organization),
            "work_country_name": country_name(task.work_country),
            "payments_enabled": settings.PAYMENTS_ENABLED,
            "can_pay": billing.can_pay_invoice(invoice, request.membership) if invoice else False,
            "can_pay_fees": any(
                billing.can_pay_invoice(fr.invoice, request.membership) for fr in fee_requests if fr.invoice
            ),
            "actions": actions,
            "thread": _mark_mine(communications.task_thread(task, request.user), request.user),
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
