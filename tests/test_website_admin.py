"""The Website admin: menu, Pages overview, SEO & meta, Pricing (read-only) and Preview & publish."""
from __future__ import annotations

import re

import pytest
from django.core.management import call_command
from django.test import Client, override_settings

from apps.accounts.models import StaffRole
from apps.website import home, services

from .conftest import login_client, make_user

NEW_SCREENS = ["/staff/website/", "/staff/website/seo/", "/staff/website/pricing/", "/staff/website/publish/"]


@pytest.fixture
def editor(db):
    return make_user("admin.editor@example.test", staff_role=StaffRole.WEBSITE_EDITOR, mfa=True, is_staff=True)


@pytest.fixture
def publisher(db):
    return make_user("admin.publisher@example.test", staff_role=StaffRole.WEBSITE_PUBLISHER, mfa=True, is_staff=True)


def _sidebar(body: str) -> str:
    return body[body.index('<aside id="sidebar"'):body.index("</aside>")]


# ---------- the menu ----------

def test_website_staff_get_the_website_menu_and_nothing_else(editor):
    side = _sidebar(login_client(editor).get("/staff/website/").content.decode())
    for label in ("Pages", "Home page", "Questions (FAQs)", "Media library", "SEO &amp; meta", "Pricing",
                  "Preview &amp; publish", "Live site"):
        assert label in side, label
    assert ">STAFF<" not in side and "Operations Queue" not in side  # no empty heading, no task screens
    assert side.count('aria-current="page"') == 1 and re.search(r'aria-current="page"><svg[^>]*><path[^>]*></path></svg>Pages<', side)


def test_the_current_section_is_highlighted(editor):
    side = _sidebar(login_client(editor).get("/staff/website/seo/").content.decode())
    assert re.search(r'aria-current="page"><svg[^>]*><path[^>]*></path></svg>SEO &amp; meta<', side)


def test_staff_who_cannot_edit_the_website_never_see_the_menu(db):
    support = make_user("admin.support@example.test", staff_role=StaffRole.SUPPORT, mfa=True, is_staff=True)
    c = login_client(support)
    assert ">WEBSITE<" not in c.get("/staff/").content.decode()
    for url in NEW_SCREENS:
        assert c.get(url).status_code == 302, url
    for url in NEW_SCREENS:
        assert Client().get(url).status_code == 302, url


def test_admins_see_the_website_menu_only_inside_the_website_area(db):
    admin = make_user("admin.admin@example.test", staff_role=StaffRole.ADMIN, mfa=True, is_staff=True)
    c = login_client(admin)
    assert ">WEBSITE<" not in _sidebar(c.get("/staff/").content.decode())
    assert ">WEBSITE<" in _sidebar(c.get("/staff/website/").content.decode())


# ---------- Pages ----------

def test_pages_lists_the_home_page_first_with_tabs_and_counts(editor, publisher):
    page, revision = services.create_page(slug="about", title="About us", summary="Who we are", body="x",
                                          actor=editor)
    services.publish(page, revision, actor=publisher)
    services.create_page(slug="careers", title="Careers", summary="Join us", body="x", actor=editor)
    body = login_client(editor).get("/staff/website/").content.decode()
    table = body[body.index("<tbody>"):body.index("</tbody>")]
    assert table.index(">Home<") < table.index(">About us<") < table.index(">Careers<")
    assert "All pages (3)" in body and "Published (2)" in body and "Drafts &amp; changes (1)" in body
    drafts = login_client(editor).get("/staff/website/?tab=drafts").content.decode()
    drafts_table = drafts[drafts.index("<tbody>"):drafts.index("</tbody>")]
    assert ">Careers<" in drafts_table and ">About us<" not in drafts_table


def test_saved_changes_on_a_live_page_show_as_waiting(editor, publisher):
    page, revision = services.create_page(slug="about", title="About us", summary="", body="x", actor=editor)
    services.publish(page, revision, actor=publisher)
    services.save_draft(page, title="About us (new)", summary="", body="y", actor=editor)
    home.save_draft(home.defaults(), actor=editor)
    body = login_client(editor).get("/staff/website/?tab=drafts").content.decode()
    assert body.count("Changes to publish") == 2  # the page and the home page


def test_search_finds_pages_by_title_or_address(editor):
    services.create_page(slug="careers", title="Join the team", summary="", body="x", actor=editor)
    c = login_client(editor)
    for q in ("join", "careers"):
        table = c.get(f"/staff/website/?q={q}").content.decode()
        assert ">Join the team<" in table, q
    assert "No pages match" in c.get("/staff/website/?q=zzz").content.decode()
    assert "&lt;script&gt;" in c.get("/staff/website/?q=<script>").content.decode()  # the search is shown escaped


# ---------- SEO & meta ----------

def test_seo_overview_flags_what_needs_fixing(editor):
    services.create_page(slug="about", title="About", summary="", body="x", actor=editor)
    body = login_client(editor).get("/staff/website/seo/").content.decode()
    assert "No search description: add one of 50–160 characters" in body
    assert "Set APP_BASE_URL to the real https:// address" in body and "not verified yet" in body
    assert "/sitemap.xml" in body


@override_settings(APP_BASE_URL="https://bandaessentials.com", GOOGLE_SITE_VERIFICATION="x")
def test_seo_overview_is_quiet_when_the_site_is_set_up(editor):
    body = login_client(editor).get("/staff/website/seo/").content.decode()
    assert "Set APP_BASE_URL" not in body and "verification set" in body


# ---------- Pricing ----------

def test_pricing_shows_the_billing_plans_read_only(editor):
    from apps.billing.models import Plan

    call_command("seed_plans", stdout=open("/dev/null", "w"))  # noqa: SIM115
    Plan.objects.filter(slug="starter").update(price_minor=7_000_000)
    c = login_client(editor)
    body = c.get("/staff/website/pricing/").content.decode()
    assert "₦70,000" in body and "Read-only" in body
    assert "<form" not in body[body.index('id="main"'):]  # nothing here changes a price
    assert c.post("/staff/website/pricing/").status_code == 405


# ---------- Preview & publish ----------

def test_publish_queue_lists_what_is_waiting_and_publishers_publish_from_it(editor, publisher):
    page, _ = services.create_page(slug="careers", title="Careers", summary="", body="x", actor=editor)
    draft = home.save_draft({**home.defaults(), "hero_title": "Queued headline"}, actor=editor)
    as_editor = login_client(editor).get("/staff/website/publish/").content.decode()
    assert "Careers" in as_editor and "Home" in as_editor
    assert ">Publish<" not in as_editor  # editors preview; publishers publish
    pub = login_client(publisher)
    queue = pub.get("/staff/website/publish/").content.decode()
    assert queue.count(">Publish<") == 2
    pub.post(f"/staff/website/home/{draft.pk}/publish/")
    assert "Queued headline" in Client().get("/").content.decode()
    assert "Nothing waiting" not in pub.get("/staff/website/publish/").content.decode()  # the page is still there
    page.refresh_from_db()
    assert page.published_revision_id is None


def test_empty_queue(publisher):
    assert "Nothing waiting" in login_client(publisher).get("/staff/website/publish/").content.decode()
