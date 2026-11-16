from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import staff_permission_required
from apps.accounts.permissions import (
    LEADS_VIEW,
    WEBSITE_EDIT,
    WEBSITE_PUBLISH,
    has_perm,
)
from apps.core.exceptions import DomainError
from apps.core.views import handle_domain_error

from . import services
from .forms import PageCreateForm, PageDraftForm
from .models import ContactMessage, ContactStatus, Page, PageRevision


def _page(page_id) -> Page:
    page = Page.objects.select_related("published_revision").filter(pk=page_id).first()
    if page is None:
        raise Http404("Page not found")
    return page


def _revision(page: Page, revision_id):
    if not revision_id:
        return None
    try:
        return page.revisions.filter(pk=revision_id).first()
    except (ValueError, ValidationError):
        return None


@staff_permission_required(WEBSITE_EDIT)
@require_GET
def page_list(request):
    pages = list(Page.objects.select_related("published_revision").order_by("slug"))
    latest: dict = {}
    for revision in PageRevision.objects.order_by("page_id", "-revision_number"):
        latest.setdefault(revision.page_id, revision)
    rows = [{"page": page, "latest": latest.get(page.pk)} for page in pages]
    return render(request, "staff/website/pages.html", {"rows": rows})


@staff_permission_required(WEBSITE_EDIT)
@require_http_methods(["GET", "POST"])
def page_create(request):
    form = PageCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            page, revision = services.create_page(
                slug=form.cleaned_data["slug"],
                title=form.cleaned_data["title"],
                summary=form.cleaned_data["summary"],
                body=form.cleaned_data["body"],
                actor=request.user,
            )
        except DomainError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, f"Created /{page.slug}/ as draft v{revision.revision_number}.")
            return redirect("staff-website-history", page_id=page.pk)
    return render(request, "staff/website/page_form.html", {"form": form, "mode": "create"})


@staff_permission_required(WEBSITE_EDIT)
@require_http_methods(["GET", "POST"])
def page_edit(request, page_id):
    page = _page(page_id)
    latest = page.revisions.select_related("created_by").first()
    if latest is None:  # pragma: no cover - every page starts with a revision
        raise Http404("Page not found")
    if request.method == "POST":
        form = PageDraftForm(request.POST)
        if form.is_valid():
            try:
                revision = services.save_draft(
                    page,
                    title=form.cleaned_data["title"],
                    summary=form.cleaned_data["summary"],
                    body=form.cleaned_data["body"],
                    actor=request.user,
                )
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(
                    request, f"Saved draft v{revision.revision_number}. Publish it when ready."
                )
                return redirect("staff-website-history", page_id=page.pk)
    else:
        form = PageDraftForm(
            initial={
                "title": latest.title,
                "summary": latest.summary,
                "body": latest.body,
            }
        )
    return render(
        request,
        "staff/website/page_form.html",
        {"form": form, "mode": "edit", "page": page, "latest": latest},
    )


@staff_permission_required(WEBSITE_EDIT)
@require_GET
def page_preview(request, page_id):
    page = _page(page_id)
    revision = _revision(page, request.GET.get("revision")) or page.revisions.first()
    if revision is None:  # pragma: no cover - every page starts with a revision
        raise Http404("Page not found")
    return render(
        request,
        "public/page.html",
        {
            "page": page,
            "revision": revision,
            "preview": True,
            "site_pages": services.published_pages(),
        },
    )


@staff_permission_required(WEBSITE_EDIT)
@require_GET
def page_history(request, page_id):
    page = _page(page_id)
    revisions = page.revisions.select_related("created_by")
    return render(
        request,
        "staff/website/page_history.html",
        {
            "page": page,
            "revisions": revisions,
            "published_revision_id": page.published_revision_id,
            "can_publish": has_perm(request.user, WEBSITE_PUBLISH),
        },
    )


@staff_permission_required(WEBSITE_PUBLISH)
@require_POST
def page_publish(request, page_id):
    page = _page(page_id)
    revision = _revision(page, request.POST.get("revision")) or page.revisions.first()
    if revision is None:  # pragma: no cover - every page starts with a revision
        raise Http404("Page not found")
    try:
        services.publish(page, revision, actor=request.user)
    except DomainError as exc:
        return handle_domain_error(
            request, exc, redirect_to=reverse("staff-website-history", args=[page.pk])
        )
    messages.success(request, f"Published /{page.slug}/ at v{revision.revision_number}.")
    return redirect("staff-website-pages")


@staff_permission_required(WEBSITE_PUBLISH)
@require_POST
def page_unpublish(request, page_id):
    page = _page(page_id)
    try:
        services.unpublish(page, actor=request.user)
    except DomainError as exc:
        return handle_domain_error(
            request, exc, redirect_to=reverse("staff-website-history", args=[page.pk])
        )
    messages.info(request, f"/{page.slug}/ is no longer public.")
    return redirect("staff-website-pages")


@staff_permission_required(WEBSITE_EDIT)
@require_POST
def page_restore(request, page_id):
    page = _page(page_id)
    revision = _revision(page, request.POST.get("revision"))
    if revision is None:
        raise Http404("Revision not found")
    try:
        restored = services.restore_revision(page, revision, actor=request.user)
    except DomainError as exc:
        return handle_domain_error(
            request, exc, redirect_to=reverse("staff-website-history", args=[page.pk])
        )
    messages.success(
        request,
        f"Restored v{revision.revision_number} as draft v{restored.revision_number}.",
    )
    return redirect("staff-website-history", page_id=page.pk)


@staff_permission_required(LEADS_VIEW)
@require_GET
def leads(request):
    status = (request.GET.get("status") or "").strip()
    if status not in ContactStatus.values:
        status = ""
    enquiries = ContactMessage.objects.select_related("handled_by")
    if status:
        enquiries = enquiries.filter(status=status)
    counts = ContactMessage.objects.aggregate(
        new=Count("id", filter=Q(status=ContactStatus.NEW)),
        handled=Count("id", filter=Q(status=ContactStatus.HANDLED)),
        spam=Count("id", filter=Q(status=ContactStatus.SPAM)),
    )
    return render(
        request,
        "staff/website/leads.html",
        {
            "enquiries": enquiries[:200],
            "statuses": ContactStatus.choices,
            "selected_status": status,
            "counts": counts,
        },
    )


@staff_permission_required(LEADS_VIEW)
@require_POST
def lead_update(request, contact_id):
    contact = ContactMessage.objects.filter(pk=contact_id).first()
    if contact is None:
        raise Http404("Enquiry not found")
    try:
        services.update_lead(contact, status=request.POST.get("status", ""), actor=request.user)
    except DomainError as exc:
        messages.error(request, exc.message)
    else:
        messages.success(request, "Enquiry updated.")
    return redirect("staff-leads")
