from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.text import slugify

from apps.audit import services as audit
from apps.core.exceptions import ValidationFailure

from .models import ContactMessage, ContactStatus, MenuPlacement, Page, PageRevision

RESERVED_SLUGS = frozenset(
    {
        "admin",
        "app",
        "contact",
        "favicon.ico",
        "health",
        "login",
        "logout",
        "media",
        "password-reset",
        "robots.txt",
        "services",
        "signup",
        "site-images",
        "sitemap.xml",
        "staff",
        "static",
        "step-up",
        "verify-email",
    }
)

MAX_BODY_LENGTH = 20_000
MAX_META_DESCRIPTION = 160
# The header has room for Home, Services and Contact plus a few pages.
MAX_HEADER_PAGES = 3


def _clean_slug(raw: str) -> str:
    slug = slugify((raw or "").strip())
    if not slug:
        raise ValidationFailure("Enter a page address.")
    if len(slug) > 120:
        raise ValidationFailure("That page address is too long.")
    if slug in RESERVED_SLUGS:
        raise ValidationFailure("That address is reserved. Choose another.")
    return slug


def _clean_content(*, title: str, summary: str, body: str, meta_description: str = "",
                   hide_from_search: bool = False, share_image=None) -> dict:
    meta_description = " ".join((meta_description or "").split())
    if len(meta_description) > MAX_META_DESCRIPTION:
        raise ValidationFailure(f"Keep the search description to {MAX_META_DESCRIPTION} characters or fewer.")
    title = (title or "").strip()
    summary = (summary or "").strip()
    body = (body or "").strip()
    if not title:
        raise ValidationFailure("Give the page a title.")
    if len(title) > 160:
        raise ValidationFailure("Keep the title to 160 characters or fewer.")
    if len(summary) > 255:
        raise ValidationFailure("Keep the summary to 255 characters or fewer.")
    if not body:
        raise ValidationFailure("Add some content before saving.")
    if len(body) > MAX_BODY_LENGTH:
        raise ValidationFailure("That content is too long.")
    return {"title": title, "summary": summary, "body": body,
            "meta_description": meta_description, "hide_from_search": bool(hide_from_search),
            "share_image": share_image}


@transaction.atomic
def create_page(*, slug: str, title: str, summary: str, body: str, actor, meta_description: str = "",
                hide_from_search: bool = False, share_image=None):
    slug = _clean_slug(slug)
    content = _clean_content(title=title, summary=summary, body=body, meta_description=meta_description,
                             hide_from_search=hide_from_search, share_image=share_image)
    if Page.objects.filter(slug=slug).exists():
        raise ValidationFailure("A page with that address already exists.")
    try:
        with transaction.atomic():
            page = Page.objects.create(slug=slug)
    except IntegrityError as exc:
        raise ValidationFailure("A page with that address already exists.") from exc
    revision = PageRevision.objects.create(
        page=page, revision_number=1, created_by=actor, **content
    )
    audit.record(
        action="website.page_created",
        actor=actor,
        resource_type="website_page",
        resource_id=str(page.pk),
        reason=slug,
    )
    return page, revision


def _next_revision(page: Page, *, content: dict, actor, action: str) -> PageRevision:
    locked = Page.objects.select_for_update().get(pk=page.pk)
    last = locked.revisions.aggregate(last=Max("revision_number"))["last"] or 0
    revision = PageRevision.objects.create(
        page=locked, revision_number=last + 1, created_by=actor, **content
    )
    audit.record(
        action=action,
        actor=actor,
        resource_type="website_page",
        resource_id=str(locked.pk),
        reason=f"v{revision.revision_number}",
    )
    return revision


@transaction.atomic
def save_draft(page: Page, *, title: str, summary: str, body: str, actor, meta_description: str = "",
               hide_from_search: bool = False, share_image=None) -> PageRevision:
    content = _clean_content(title=title, summary=summary, body=body, meta_description=meta_description,
                             hide_from_search=hide_from_search, share_image=share_image)
    return _next_revision(page, content=content, actor=actor, action="website.draft_saved")


@transaction.atomic
def restore_revision(page: Page, revision: PageRevision, *, actor) -> PageRevision:
    if revision.page_id != page.pk:
        raise ValidationFailure("That revision does not belong to this page.")
    content = _clean_content(
        title=revision.title, summary=revision.summary, body=revision.body,
        meta_description=revision.meta_description, hide_from_search=revision.hide_from_search,
        share_image=revision.share_image,
    )
    return _next_revision(
        page, content=content, actor=actor, action="website.revision_restored"
    )


@transaction.atomic
def publish(page: Page, revision: PageRevision, *, actor) -> Page:
    if revision.page_id != page.pk:
        raise ValidationFailure("That revision does not belong to this page.")
    locked = Page.objects.select_for_update().get(pk=page.pk)
    now = timezone.now()
    if revision.published_at is None:
        revision.published_at = now
        revision.save(update_fields=["published_at"])
    locked.published_revision = revision
    locked.published_at = now
    locked.save(update_fields=["published_revision", "published_at", "updated_at"])
    audit.record(
        action="website.page_published",
        actor=actor,
        resource_type="website_page",
        resource_id=str(locked.pk),
        reason=f"v{revision.revision_number}",
    )
    return locked


@transaction.atomic
def unpublish(page: Page, *, actor) -> Page:
    locked = Page.objects.select_for_update().get(pk=page.pk)
    locked.published_revision = None
    locked.published_at = None
    locked.save(update_fields=["published_revision", "published_at", "updated_at"])
    audit.record(
        action="website.page_unpublished",
        actor=actor,
        resource_type="website_page",
        resource_id=str(locked.pk),
    )
    return locked


def published_page(slug: str) -> Page | None:
    return (
        Page.objects.select_related("published_revision")
        .filter(slug=slug, published_revision__isnull=False)
        .first()
    )


def published_pages():
    """Published pages for the footer menu, in the order publishers chose."""
    return menu_pages(MenuPlacement.FOOTER)


def menu_pages(placement: str):
    """Published pages linked from the footer (which includes header pages) or the header."""
    places = [MenuPlacement.HEADER] if placement == MenuPlacement.HEADER else [MenuPlacement.HEADER, MenuPlacement.FOOTER]
    return (
        Page.objects.select_related("published_revision")
        .filter(published_revision__isnull=False, menu__in=places)
        .order_by("menu_order", "slug")
    )


@transaction.atomic
def set_menu(page: Page, *, menu: str, menu_order: int, actor) -> Page:
    """Where a page is linked from. It changes the live site, so it needs the publish permission."""
    from apps.accounts.permissions import WEBSITE_PUBLISH, require_perm

    require_perm(actor, WEBSITE_PUBLISH)
    if menu not in MenuPlacement.values:
        raise ValidationFailure("Choose where the page appears.")
    if not isinstance(menu_order, int) or not 0 <= menu_order <= 999:
        raise ValidationFailure("The menu position must be a whole number from 0 to 999.")
    locked = Page.objects.select_for_update().get(pk=page.pk)
    header_full = Page.objects.filter(menu=MenuPlacement.HEADER).exclude(pk=locked.pk).count() >= MAX_HEADER_PAGES
    if menu == MenuPlacement.HEADER and locked.menu != MenuPlacement.HEADER and header_full:
        raise ValidationFailure(f"The header has room for {MAX_HEADER_PAGES} pages. Move one to the footer first.")
    before = (locked.menu, locked.menu_order)
    locked.menu, locked.menu_order = menu, menu_order
    locked.save(update_fields=["menu", "menu_order", "updated_at"])
    audit.record(
        action="website.menu_changed",
        actor=actor,
        resource_type="website_page",
        resource_id=str(locked.pk),
        reason=f"{before[0]}/{before[1]} -> {menu}/{menu_order}",
    )
    return locked


def submit_contact_message(
    *, name: str, email: str, phone: str, company: str, message: str, ip: str
) -> ContactMessage:
    contact = ContactMessage.objects.create(
        name=name,
        email=email,
        phone=phone,
        company=company,
        message=message,
        source_ip_hash=(
            salted_hmac("website.contact_ip", ip).hexdigest() if ip else ""
        ),
    )
    audit.record(
        action="website.contact_received",
        resource_type="contact_message",
        resource_id=str(contact.pk),
    )
    return contact


@transaction.atomic
def update_lead(contact: ContactMessage, *, status: str, actor) -> ContactMessage:
    if status not in ContactStatus.values:
        raise ValidationFailure("Choose a valid status.")
    contact.status = status
    if status == ContactStatus.NEW:
        contact.handled_by = None
        contact.handled_at = None
    else:
        contact.handled_by = actor
        contact.handled_at = timezone.now()
    contact.save(update_fields=["status", "handled_by", "handled_at", "updated_at"])
    audit.record(
        action="website.lead_updated",
        actor=actor,
        resource_type="contact_message",
        resource_id=str(contact.pk),
        reason=status,
    )
    return contact
