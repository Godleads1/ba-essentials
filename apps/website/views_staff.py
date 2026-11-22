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

from . import home, images, services
from .forms import (
    ImageUploadForm,
    PageCreateForm,
    PageDraftForm,
    PageMenuForm,
    faq_formset_class,
    home_copy_form_class,
)
from .models import (
    ContactMessage,
    ContactStatus,
    HomePage,
    HomeRevision,
    MenuPlacement,
    Page,
    PageImage,
    PageRevision,
)


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


def _website_rows() -> list[dict]:
    """Every page staff manage, the home page first: what the Pages table, SEO and publish screens show."""
    rows = []
    state = HomePage.objects.select_related("published_revision").filter(pk=1).first()
    live = state.published_revision if state else None
    newest = HomeRevision.objects.select_related("created_by").order_by("-number").first()
    rows.append({
        "kind": "home", "title": "Home", "subtitle": "Landing page", "path": "/",
        "status": "changes" if newest and (live is None or newest.number > live.number) else "published",
        "updated_at": newest.created_at if newest else None, "updated_by": newest.created_by if newest else None,
        "view_url": "/", "edit_url": reverse("staff-website-home"),
        "history_url": reverse("staff-website-home-history"),
        "preview_url": reverse("staff-website-home-preview", args=[newest.pk]) if newest else "/",
        "draft": newest if newest and (live is None or newest.number > live.number) else None,
        "seo": home.Copy(newest.content if newest else None),
    })
    pages = list(Page.objects.select_related("published_revision").order_by("slug"))
    latest: dict = {}
    for revision in PageRevision.objects.select_related("created_by", "share_image").order_by(
            "page_id", "-revision_number"):
        latest.setdefault(revision.page_id, revision)
    for page in pages:
        newest_page = latest.get(page.pk)
        published = page.published_revision_id is not None
        waiting = newest_page and page.published_revision_id != newest_page.pk
        rows.append({
            "kind": "page", "page": page, "revision": newest_page,
            "title": newest_page.title if newest_page else "Untitled",
            "subtitle": newest_page.summary if newest_page else "", "path": f"/{page.slug}/",
            "status": ("changes" if waiting else "published") if published else "draft",
            "updated_at": newest_page.created_at if newest_page else page.updated_at,
            "updated_by": newest_page.created_by if newest_page else None,
            "view_url": f"/{page.slug}/" if published else reverse("staff-website-preview", args=[page.pk]),
            "edit_url": reverse("staff-website-edit", args=[page.pk]),
            "history_url": reverse("staff-website-history", args=[page.pk]),
            "preview_url": reverse("staff-website-preview", args=[page.pk]),
            "draft": newest_page if waiting else None,
        })
    return rows


@staff_permission_required(WEBSITE_EDIT)
@require_GET
def page_list(request):
    rows = _website_rows()
    counts = {"all": len(rows), "published": sum(r["status"] != "draft" for r in rows),
              "drafts": sum(r["status"] == "draft" for r in rows),
              "waiting": sum(r["status"] == "changes" for r in rows)}
    tab = request.GET.get("tab", "all")
    if tab == "published":
        rows = [r for r in rows if r["status"] != "draft"]
    elif tab == "drafts":
        rows = [r for r in rows if r["status"] in ("draft", "changes")]
    else:
        tab = "all"
    query = " ".join(request.GET.get("q", "").split())[:100]
    if query:
        needle = query.lower()
        rows = [r for r in rows if needle in r["title"].lower() or needle in r["path"].lower()
                or needle in (r["subtitle"] or "").lower()]
    return render(request, "banda/staff/website/pages.html", {
        "rows": rows, "counts": counts, "tab": tab, "query": query,
        "can_publish": has_perm(request.user, WEBSITE_PUBLISH),
        "image_count": PageImage.objects.count(),
    })


@staff_permission_required(WEBSITE_EDIT)
@require_GET
def seo_overview(request):
    """Every page's search title, description and settings in one place, with what to fix."""
    from django.conf import settings

    from . import seo

    rows = []
    for row in _website_rows():
        if row["kind"] == "home":
            copy = row["seo"]
            title, description = copy.plain("seo_title"), copy.plain("seo_description")
            hidden, image = False, copy.share_image
        else:
            revision = row["revision"]
            title = f"{revision.title} · banda — B & A Essentials" if revision else ""
            description = (revision.meta_description or revision.summary or revision.title) if revision else ""
            hidden = bool(revision and revision.hide_from_search)
            image = revision.share_image if revision else None
        problems = []
        if len(title) > 70:
            problems.append("Title is long: Google may cut it off.")
        own = row["kind"] == "home" or bool(row["revision"] and row["revision"].meta_description)
        if not own:
            problems.append("No search description: add one of 50–160 characters (the summary is used for now).")
        elif len(description) < 50:
            problems.append("Search description is short: aim for 50–160 characters.")
        rows.append({**row, "seo_title": title, "seo_description": description, "hidden": hidden,
                     "share_image": image, "problems": problems})
    site = {
        "base_url": seo.site_url(),
        "base_url_ok": seo.site_url().startswith("https://") and "localhost" not in seo.site_url(),
        "google": bool(settings.GOOGLE_SITE_VERIFICATION), "bing": bool(settings.BING_SITE_VERIFICATION),
        "city": settings.BUSINESS_CITY, "phone": settings.BUSINESS_PHONE, "same_as": settings.SEO_SAME_AS,
    }
    return render(request, "banda/staff/website/seo.html", {"rows": rows, "site": site})


@staff_permission_required(WEBSITE_EDIT)
@require_GET
def pricing_overview(request):
    """The plans and prices the website shows. Read-only: they come from billing."""
    from apps.billing.models import Plan
    from apps.core.views import _landing_prices

    data, _ = _landing_prices()
    def money(entry):
        if not entry:
            return "—"
        symbol = {"NGN": "₦", "USD": "$"}.get(entry["currency"], entry["currency"] + " ")
        return f"{symbol}{entry['month'] // 100:,}"

    plans = []
    for plan in Plan.objects.filter(is_active=True).order_by("rank", "price_minor"):
        prices = data["plans"].get(plan.slug, {})
        free = plan.price_minor == 0
        plans.append({"plan": plan, "ng": "Free" if free else money(prices.get("ng")),
                      "intl": "Free" if free else money(prices.get("intl"))})
    legal = data["legal"].get("ng")
    return render(request, "banda/staff/website/pricing.html", {
        "plans": plans, "discount": data["discount"],
        "legal_ng": f"₦{legal['minor'] // 100:,}" if legal else "",
    })


@staff_permission_required(WEBSITE_EDIT)
@require_GET
def publish_queue(request):
    """Everything saved but not live yet: preview it, then publish."""
    rows = [r for r in _website_rows() if r["status"] in ("draft", "changes")]
    return render(request, "banda/staff/website/publish.html", {
        "rows": rows, "can_publish": has_perm(request.user, WEBSITE_PUBLISH),
    })


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
                meta_description=form.cleaned_data["meta_description"],
                hide_from_search=form.cleaned_data["hide_from_search"],
                share_image=form.cleaned_data["share_image"],
                actor=request.user,
            )
        except DomainError as exc:
            form.add_error(None, exc.message)
        else:
            messages.success(request, f"Created /{page.slug}/ as draft v{revision.revision_number}.")
            return redirect("staff-website-history", page_id=page.pk)
    return render(request, "banda/staff/website/page_form.html", {"form": form, "mode": "create"})


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
                    meta_description=form.cleaned_data["meta_description"],
                    hide_from_search=form.cleaned_data["hide_from_search"],
                    share_image=form.cleaned_data["share_image"],
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
                "meta_description": latest.meta_description,
                "hide_from_search": latest.hide_from_search,
                "share_image": latest.share_image_id,
            }
        )
    return render(
        request,
        "banda/staff/website/page_form.html",
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
        "banda/public/page.html",
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
        "banda/staff/website/page_history.html",
        {
            "page": page,
            "revisions": revisions,
            "published_revision_id": page.published_revision_id,
            "can_publish": has_perm(request.user, WEBSITE_PUBLISH),
            "menu_form": PageMenuForm(initial={"menu": page.menu, "menu_order": page.menu_order}),
            "menu_label": MenuPlacement(page.menu).label,
        },
    )


@staff_permission_required(WEBSITE_PUBLISH)
@require_POST
def page_menu(request, page_id):
    page = _page(page_id)
    form = PageMenuForm(request.POST)
    back = reverse("staff-website-history", args=[page.pk])
    if not form.is_valid():
        messages.error(request, "Choose where the page appears and a position from 0 to 999.")
        return redirect(back)
    try:
        services.set_menu(page, menu=form.cleaned_data["menu"], menu_order=form.cleaned_data["menu_order"],
                          actor=request.user)
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to=back)
    messages.success(request, f"Menu updated for /{page.slug}/.")
    return redirect(back)


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


@staff_permission_required(WEBSITE_EDIT)
@require_http_methods(["GET", "POST"])
def image_library(request):
    form = ImageUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            image = images.upload(uploaded_file=form.cleaned_data["image"], alt_text=form.cleaned_data["alt_text"],
                                  actor=request.user)
        except DomainError as exc:
            form.add_error("image", exc.message)
        else:
            messages.success(request, "Image uploaded. Copy its line into a page to show it.")
            return redirect(f"{reverse('staff-website-images')}#image-{image.pk}")
    rows = [{"image": image, "live": images.is_live(image)} for image in PageImage.objects.select_related("uploaded_by")[:200]]
    return render(request, "banda/staff/website/images.html", {"form": form, "rows": rows})


@staff_permission_required(WEBSITE_EDIT)
@require_POST
def image_delete(request, image_id):
    image = PageImage.objects.filter(pk=image_id).first()
    if image is None:
        raise Http404("Image not found")
    try:
        images.delete(image, actor=request.user)
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to=reverse("staff-website-images"))
    messages.info(request, "Image deleted.")
    return redirect("staff-website-images")


# ---------- home page words ----------

def _home_sections(form):
    return [(title, [form[f.key] for f in fields]) for title, fields in home.SECTIONS]


@staff_permission_required(WEBSITE_EDIT)
@require_http_methods(["GET", "POST"])
def home_edit(request):
    Form, Faqs = home_copy_form_class(), faq_formset_class()
    if request.method == "POST":
        form, faqs = Form(request.POST), Faqs(request.POST, prefix="faq")
        if form.is_valid() and faqs.is_valid():
            rows = [f for f in faqs.ordered_forms if not f.cleaned_data.get("DELETE")]
            data = {**{k: v for k, v in form.cleaned_data.items() if k != "share_image"},
                    "share_image": form.cleaned_data["share_image"].pk if form.cleaned_data["share_image"] else None,
                    "faqs": [{"question": f.cleaned_data.get("question", ""), "answer": f.cleaned_data.get("answer", "")}
                             for f in rows]}
            try:
                revision = home.save_draft(data, actor=request.user)
            except DomainError as exc:
                form.add_error(None, exc.message)
            else:
                messages.success(request, f"Saved as version {revision.number}. Preview it, then publish.")
                return redirect("staff-website-home-history")
    else:
        content = home.current_content()
        form = Form(initial={**content, "share_image": content.get("share_image")})
        faqs = Faqs(prefix="faq", initial=[{**f, "ORDER": i} for i, f in enumerate(content["faqs"], 1)])
    return render(request, "banda/staff/website/home_edit.html",
                  {"form": form, "sections": _home_sections(form), "faqs": faqs})


@staff_permission_required(WEBSITE_EDIT)
@require_GET
def home_history(request):
    from django.core.paginator import Paginator

    state = HomePage.objects.filter(pk=1).first()
    page = Paginator(HomeRevision.objects.select_related("created_by"), 50).get_page(request.GET.get("page"))
    return render(request, "banda/staff/website/home_history.html", {
        "revisions": page,
        "page": page,
        "live_id": state.published_revision_id if state else None,
        "can_publish": has_perm(request.user, WEBSITE_PUBLISH),
    })


@staff_permission_required(WEBSITE_EDIT)
@require_GET
def home_preview(request, revision_id):
    from apps.core.views import landing_context

    revision = HomeRevision.objects.filter(pk=revision_id).first()
    if revision is None:
        raise Http404("Version not found")
    return render(request, "banda/public/landing.html", landing_context(home.Copy(revision.content), preview=revision))


@staff_permission_required(WEBSITE_PUBLISH)
@require_POST
def home_publish(request, revision_id):
    revision = HomeRevision.objects.filter(pk=revision_id).first()
    if revision is None:
        raise Http404("Version not found")
    try:
        home.publish(revision, actor=request.user)
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to=reverse("staff-website-home-history"))
    messages.success(request, f"Version {revision.number} is now live on the home page.")
    return redirect("staff-website-home-history")


@staff_permission_required(WEBSITE_EDIT)
@require_POST
def home_restore(request, revision_id):
    revision = HomeRevision.objects.filter(pk=revision_id).first()
    if revision is None:
        raise Http404("Version not found")
    try:
        copy = home.restore(revision, actor=request.user)
    except DomainError as exc:
        return handle_domain_error(request, exc, redirect_to=reverse("staff-website-home-history"))
    messages.info(request, f"Version {revision.number} copied to a new draft, version {copy.number}. Publish it to make it live.")
    return redirect("staff-website-home-history")


@staff_permission_required(WEBSITE_PUBLISH)
@require_POST
def home_use_original(request):
    home.use_original(actor=request.user)
    messages.info(request, "The home page is back to its original wording. Your versions are kept.")
    return redirect("staff-website-home-history")


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
        "banda/staff/website/leads.html",
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
