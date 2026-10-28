from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.text import slugify

from apps.audit import services as audit
from apps.core import ratelimit
from apps.core.exceptions import ValidationFailure

from .models import ContactMessage, Page, PageRevision

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
        "signup",
        "sitemap.xml",
        "staff",
        "static",
        "step-up",
        "verify-email",
    }
)

MAX_BODY_LENGTH = 20_000


def _clean_slug(raw: str) -> str:
    slug = slugify((raw or "").strip())
    if not slug:
        raise ValidationFailure("Enter a page address.")
    if len(slug) > 120:
        raise ValidationFailure("That page address is too long.")
    if slug in RESERVED_SLUGS:
        raise ValidationFailure("That address is reserved. Choose another.")
    return slug


def _clean_content(*, title: str, summary: str, body: str) -> dict:
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
    return {"title": title, "summary": summary, "body": body}


@transaction.atomic
def create_page(*, slug: str, title: str, summary: str, body: str, actor):
    slug = _clean_slug(slug)
    content = _clean_content(title=title, summary=summary, body=body)
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
def save_draft(page: Page, *, title: str, summary: str, body: str, actor) -> PageRevision:
    content = _clean_content(title=title, summary=summary, body=body)
    return _next_revision(page, content=content, actor=actor, action="website.draft_saved")


@transaction.atomic
def restore_revision(page: Page, revision: PageRevision, *, actor) -> PageRevision:
    if revision.page_id != page.pk:
        raise ValidationFailure("That revision does not belong to this page.")
    content = _clean_content(
        title=revision.title, summary=revision.summary, body=revision.body
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
    return (
        Page.objects.select_related("published_revision")
        .filter(published_revision__isnull=False)
        .order_by("slug")
    )


def submit_contact_message(
    *, name: str, email: str, phone: str, company: str, message: str, ip: str
) -> ContactMessage:
    contact = ContactMessage.objects.create(
        name=name,
        email=email,
        phone=phone,
        company=company,
        message=message,
        source_ip_hash=ratelimit.fingerprint(ip) if ip else "",
    )
    audit.record(
        action="website.contact_received",
        resource_type="contact_message",
        resource_id=str(contact.pk),
    )
    return contact


@transaction.atomic
def update_lead(contact: ContactMessage, *, status: str, actor) -> ContactMessage:
    from .models import ContactStatus

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
