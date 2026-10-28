from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import StaffRole
from apps.core.exceptions import ValidationFailure
from apps.website import services
from apps.website.models import ContactMessage, ContactStatus, Page, PageRevision

from .conftest import login_client, make_user


@pytest.fixture
def editor(db):
    return make_user(
        "editor@example.test", staff_role=StaffRole.WEBSITE_EDITOR, mfa=True, is_staff=True
    )


@pytest.fixture
def publisher(db):
    return make_user(
        "publisher@example.test",
        staff_role=StaffRole.WEBSITE_PUBLISHER,
        mfa=True,
        is_staff=True,
    )


@pytest.fixture
def support_user(db):
    return make_user(
        "support@example.test", staff_role=StaffRole.SUPPORT, mfa=True, is_staff=True
    )


def _make_page(editor, slug="about", title="About us", body="Synthetic body copy."):
    return services.create_page(
        slug=slug,
        title=title,
        summary="Synthetic summary.",
        body=body,
        actor=editor,
    )


def test_unpublished_drafts_are_not_public(client, editor):
    page, _revision = _make_page(editor)

    response = client.get(reverse("website-page", args=[page.slug]))

    assert response.status_code == 404


def test_published_page_is_public_and_html_is_escaped(client, editor, publisher):
    page, revision = _make_page(
        editor, body="First line\n<script>alert('x')</script>"
    )
    services.publish(page, revision, actor=publisher)

    response = client.get(reverse("website-page", args=[page.slug]))

    assert response.status_code == 200
    content = response.content.decode()
    assert "About us" in content
    assert "<script>alert('x')</script>" not in content
    assert "&lt;script&gt;" in content


def test_editor_cannot_publish_but_publisher_can(editor, publisher):
    page, revision = _make_page(editor)

    editor_client = login_client(editor)
    denied = editor_client.post(reverse("staff-website-publish", args=[page.pk]))
    assert denied.status_code == 302
    assert denied["Location"] == reverse("staff-dashboard")
    page.refresh_from_db()
    assert page.published_revision_id is None

    publisher_client = login_client(publisher)
    allowed = publisher_client.post(
        reverse("staff-website-publish", args=[page.pk]),
        {"revision": str(revision.pk)},
    )
    assert allowed.status_code == 302
    page.refresh_from_db()
    assert page.published_revision_id == revision.pk


def test_new_draft_does_not_change_the_public_page(client, editor, publisher):
    page, revision = _make_page(editor, title="About v1")
    services.publish(page, revision, actor=publisher)

    editor_client = login_client(editor)
    saved = editor_client.post(
        reverse("staff-website-edit", args=[page.pk]),
        {"title": "About v2", "summary": "", "body": "Second synthetic body."},
    )

    assert saved.status_code == 302
    assert saved["Location"] == reverse("staff-website-history", args=[page.pk])
    page.refresh_from_db()
    assert page.revisions.count() == 2
    public = client.get(reverse("website-page", args=[page.slug]))
    assert "About v1" in public.content.decode()
    assert "About v2" not in public.content.decode()

    login_client(publisher).post(reverse("staff-website-publish", args=[page.pk]))
    public = client.get(reverse("website-page", args=[page.slug]))
    assert "About v2" in public.content.decode()


def test_restore_copies_an_old_revision_into_a_new_draft(editor):
    page, first = _make_page(editor, title="First title", body="First body.")
    second = services.save_draft(
        page, title="Second title", summary="", body="Second body.", actor=editor
    )

    restored = services.restore_revision(page, first, actor=editor)

    assert restored.revision_number == second.revision_number + 1
    assert restored.title == "First title"
    assert restored.body == "First body."
    assert Page.objects.get(pk=page.pk).published_revision_id is None


def test_reserved_and_duplicate_slugs_are_rejected(editor):
    with pytest.raises(ValidationFailure):
        _make_page(editor, slug="login")
    _make_page(editor, slug="about")
    with pytest.raises(ValidationFailure):
        _make_page(editor, slug="about")
    assert Page.objects.count() == 1


def test_contact_form_stores_a_hashed_ip_and_validates_input(client):
    response = client.post(
        reverse("contact"),
        {
            "name": "Synthetic Person",
            "email": "person@example.test",
            "phone": "",
            "company": "",
            "message": "Please help with a synthetic enquiry.",
            "website": "",
        },
        REMOTE_ADDR="203.0.113.9",
    )

    assert response.status_code == 302
    assert response["Location"].endswith("?sent=1")
    contact = ContactMessage.objects.get()
    assert contact.email == "person@example.test"
    assert contact.source_ip_hash
    assert contact.source_ip_hash != "203.0.113.9"

    invalid = client.post(
        reverse("contact"),
        {
            "name": "Synthetic Person",
            "email": "not-an-email",
            "phone": "",
            "company": "",
            "message": "Please help with a synthetic enquiry.",
            "website": "",
        },
        REMOTE_ADDR="203.0.113.10",
    )
    assert invalid.status_code == 200
    assert ContactMessage.objects.count() == 1


def test_contact_honeypot_does_not_store_a_message(client):
    response = client.post(
        reverse("contact"),
        {
            "name": "Synthetic Bot",
            "email": "bot@example.test",
            "phone": "",
            "company": "",
            "message": "This should never be stored anywhere.",
            "website": "filled-by-bot",
        },
        REMOTE_ADDR="203.0.113.11",
    )

    assert response.status_code == 302
    assert ContactMessage.objects.count() == 0


def test_operations_can_manage_leads_but_support_cannot(ops_user, support_user):
    contact = ContactMessage.objects.create(
        name="Synthetic Lead",
        email="lead@example.test",
        message="Synthetic enquiry content.",
    )

    support_client = login_client(support_user)
    denied = support_client.get(reverse("staff-leads"))
    assert denied.status_code == 302
    assert denied["Location"] == reverse("staff-dashboard")

    ops_client = login_client(ops_user)
    listing = ops_client.get(reverse("staff-leads"))
    assert listing.status_code == 200
    assert "Synthetic Lead" in listing.content.decode()

    updated = ops_client.post(
        reverse("staff-lead-update", args=[contact.pk]), {"status": ContactStatus.HANDLED}
    )
    assert updated.status_code == 302
    contact.refresh_from_db()
    assert contact.status == ContactStatus.HANDLED
    assert contact.handled_by == ops_user
    assert contact.handled_at is not None


def test_landing_links_only_published_pages(editor, publisher):
    live, live_revision = _make_page(editor, slug="about", title="About live")
    services.publish(live, live_revision, actor=publisher)
    _make_page(editor, slug="private-draft", title="Private draft")

    from django.test import Client

    response = Client().get(reverse("landing"))

    assert response.status_code == 200
    content = response.content.decode()
    assert f'href="{reverse("website-page", args=["about"])}"' in content
    assert "private-draft" not in content
    assert PageRevision.objects.count() == 2
