"""Website editor: safe formatting, the site menu, search settings and where editors land."""
from __future__ import annotations

import itertools
from html.parser import HTMLParser

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import StaffRole
from apps.audit.models import AuditEvent
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.website import markup, services
from apps.website.models import MenuPlacement, Page

from .conftest import login_client, make_user

ALLOWED_TAGS = {"p", "br", "h2", "h3", "ul", "ol", "li", "strong", "em", "a"}


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _tags(html: str):
    parser = _Tags()
    parser.feed(html)
    return parser.tags


def _assert_safe(html: str):
    for tag, attrs in _tags(html):
        assert tag in ALLOWED_TAGS, (tag, html)
        if tag == "a":
            assert set(attrs) <= {"href", "rel"}, attrs
            href = attrs["href"]
            assert href.startswith(("https://", "http://", "mailto:", "tel:", "/")), href
            assert not href.startswith(("//", "/\\")), href
        else:
            assert attrs == {}, (tag, attrs)


@pytest.fixture
def editor(db):
    return make_user("editor@example.test", staff_role=StaffRole.WEBSITE_EDITOR, mfa=True, is_staff=True)


@pytest.fixture
def publisher(db):
    return make_user("publisher@example.test", staff_role=StaffRole.WEBSITE_PUBLISHER, mfa=True, is_staff=True)


def _live(editor, publisher, slug="about", title="About us", body="Synthetic body.", **extra):
    page, revision = services.create_page(slug=slug, title=title, summary="Synthetic summary.", body=body,
                                          actor=editor, **extra)
    services.publish(page, revision, actor=publisher)
    return page


# ---------- formatting ----------

def test_formatting_becomes_the_expected_html():
    html = markup.render(
        "## What we do\n\nWe handle **admin** and *legal review*.\nSecond line.\n\n"
        "### Ways to reach us\n\n- [Our services](/services/)\n- [Email](mailto:hello@example.test)\n"
        "- [Call](tel:+2348000000000)\n\n1. Sign up\n2. Post a task\n\nSee [partner](https://partner.example)."
    )
    assert "<h2>What we do</h2>" in html and "<h3>Ways to reach us</h3>" in html
    assert "<p>We handle <strong>admin</strong> and <em>legal review</em>.<br>Second line.</p>" in html
    assert '<li><a href="/services/">Our services</a></li>' in html
    assert '<a href="mailto:hello@example.test">Email</a>' in html and 'href="tel:+2348000000000"' in html
    assert "<ol><li>Sign up</li><li>Post a task</li></ol>" in html
    assert '<a href="https://partner.example" rel="noopener noreferrer">partner</a>' in html
    _assert_safe(html)


def test_plain_text_pages_written_before_formatting_still_read_the_same():
    html = markup.render("First line\nsecond line\n\nNew paragraph with 3 * 4 = 12 and a_b.")
    assert html == "<p>First line<br>second line</p>\n<p>New paragraph with 3 * 4 = 12 and a_b.</p>"


ATTACKS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "<a href=\"javascript:alert(1)\">x</a>",
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert`1`)",
    "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "[x](vbscript:msgbox)",
    "[x](//evil.example/phish)",
    "[x](/\\evil.example)",
    "[x](https://ok.example/\"onmouseover=\"alert`1`)",
    "[x](https://ok.example/**b**)",
    "[**x**](https://ok.example/'><script>)",
    "\x000\x00 [a](https://ok.example)",
    "## <svg onload=alert(1)>",
    "- <iframe src=//evil.example>",
    "**<b>bold</b>**",
    "[x](https:evil.example)",
    "[x](http:/evil.example)",
]


@pytest.mark.parametrize("attack", ATTACKS)
def test_nothing_an_editor_types_becomes_live_html(attack):
    html = markup.render(attack)
    _assert_safe(html)  # only the allowed tags, no event attributes, only safe link targets
    links = [attrs["href"] for tag, attrs in _tags(html) if tag == "a"]
    assert all(href.startswith(("https://ok.example", "/services")) for href in links), links


def test_mixed_attacks_in_any_order_stay_safe():
    pieces = ["**", "*", "[", "](", ")", "<script>", "javascript:", "//evil", "\n", "\n\n", "- ", "## ", "\"", "x"]
    for combo in itertools.product(pieces, repeat=3):
        _assert_safe(markup.render("".join(combo)))


def test_published_page_uses_the_formatting(editor, publisher):
    client = Client()  # a visitor, not signed in
    _live(editor, publisher, body="## Our team\n\n**Real people** check every task.")
    body = client.get("/about/").content.decode()
    assert "<h2>Our team</h2>" in body and "<strong>Real people</strong>" in body


# ---------- search settings ----------

def test_search_description_noindex_and_link_previews(editor, publisher):
    client = Client()  # a visitor, not signed in
    _live(editor, publisher, meta_description="Who we are, in one line for Google.")
    body = client.get("/about/").content.decode()
    assert '<meta name="description" content="Who we are, in one line for Google." />' in body
    assert 'property="og:title" content="About us"' in body and 'rel="canonical"' in body
    assert 'name="robots"' not in body

    _live(editor, publisher, slug="partner-offer", title="Partner offer", hide_from_search=True)
    hidden = client.get("/partner-offer/").content.decode()
    assert '<meta name="robots" content="noindex" />' in hidden
    assert 'content="Synthetic summary."' in hidden  # no description given: the summary is used


def test_search_description_is_limited(editor):
    with pytest.raises(ValidationFailure):
        services.create_page(slug="long", title="Long", summary="", body="x", actor=editor,
                             meta_description="x" * 161)


def test_editor_saves_search_settings_and_restore_keeps_them(editor):
    page, first = services.create_page(slug="faq", title="FAQ", summary="", body="v1", actor=editor)
    response = login_client(editor).post(f"/staff/website/{page.pk}/", {
        "title": "FAQ", "summary": "", "body": "v2", "meta_description": "Answers.", "hide_from_search": "on"})
    assert response.status_code == 302
    latest = page.revisions.first()
    assert latest.meta_description == "Answers." and latest.hide_from_search is True
    restored = services.restore_revision(page, latest, actor=editor)
    assert restored.meta_description == "Answers." and restored.hide_from_search is True
    assert first.meta_description == ""


def test_preview_is_never_indexed_or_given_a_canonical_link(editor):
    page, _ = services.create_page(slug="draft", title="Draft", summary="", body="x", actor=editor)
    body = login_client(editor).get(f"/staff/website/{page.pk}/preview/").content.decode()
    assert '<meta name="robots" content="noindex" />' in body and 'rel="canonical"' not in body


# ---------- the site menu ----------

def test_footer_follows_the_chosen_order_and_none_hides_the_link(editor, publisher):
    client = Client()  # a visitor, not signed in
    about = _live(editor, publisher, slug="about", title="About us")
    faq = _live(editor, publisher, slug="faq", title="Questions")
    services.set_menu(faq, menu=MenuPlacement.FOOTER, menu_order=1, actor=publisher)
    body = client.get("/contact/").content.decode()
    assert body.index(">Questions<") < body.index(">About us<")

    services.set_menu(about, menu=MenuPlacement.NONE, menu_order=1, actor=publisher)
    body = client.get("/contact/").content.decode()
    assert ">About us<" not in body and client.get("/about/").status_code == 200  # still reachable by link


def test_header_pages_appear_in_both_headers_and_are_capped_at_three(editor, publisher):
    client = Client()  # a visitor, not signed in
    pages = [_live(editor, publisher, slug=f"p{i}", title=f"Header page {i}") for i in range(4)]
    for page in pages[:3]:
        services.set_menu(page, menu=MenuPlacement.HEADER, menu_order=10, actor=publisher)
    with pytest.raises(ValidationFailure):
        services.set_menu(pages[3], menu=MenuPlacement.HEADER, menu_order=10, actor=publisher)
    for url in ("/", "/contact/"):
        assert "Header page 0" in client.get(url).content.decode()
    draft, _ = services.create_page(slug="unpublished", title="Secret draft", summary="", body="x", actor=editor)
    Page.objects.filter(pk=draft.pk).update(menu=MenuPlacement.HEADER)
    assert "Secret draft" not in client.get("/").content.decode()  # drafts never appear in a menu


def test_only_publishers_change_the_menu(editor, publisher):
    page = _live(editor, publisher)
    response = login_client(editor).post(f"/staff/website/{page.pk}/menu/", {"menu": "header", "menu_order": "1"})
    assert response.status_code == 302 and response["Location"] == reverse("staff-dashboard")
    page.refresh_from_db()
    assert page.menu == MenuPlacement.FOOTER
    with pytest.raises(PermissionFailure):
        services.set_menu(page, menu=MenuPlacement.HEADER, menu_order=1, actor=editor)

    ok = login_client(publisher).post(f"/staff/website/{page.pk}/menu/", {"menu": "header", "menu_order": "5"})
    assert ok["Location"] == f"/staff/website/{page.pk}/history/"
    page.refresh_from_db()
    assert (page.menu, page.menu_order) == (MenuPlacement.HEADER, 5)
    assert AuditEvent.objects.filter(action="website.menu_changed", resource_id=str(page.pk)).exists()


@pytest.mark.parametrize("data", [{"menu": "sidebar", "menu_order": "1"}, {"menu": "footer", "menu_order": "5000"},
                                  {"menu": "footer", "menu_order": "-1"}, {"menu": "footer"}])
def test_bad_menu_values_are_refused(editor, publisher, data):
    page = _live(editor, publisher)
    login_client(publisher).post(f"/staff/website/{page.pk}/menu/", data)
    page.refresh_from_db()
    assert (page.menu, page.menu_order) == (MenuPlacement.FOOTER, 100)


def test_editors_see_the_menu_setting_but_cannot_change_it(editor, publisher):
    page = _live(editor, publisher)
    as_editor = login_client(editor).get(f"/staff/website/{page.pk}/history/").content.decode()
    assert "Footer only" in as_editor and "staff-website-menu" not in as_editor and "/menu/" not in as_editor
    as_publisher = login_client(publisher).get(f"/staff/website/{page.pk}/history/").content.decode()
    assert f"/staff/website/{page.pk}/menu/" in as_publisher


# ---------- where staff land ----------

@pytest.mark.parametrize("role", [StaffRole.WEBSITE_EDITOR, StaffRole.WEBSITE_PUBLISHER])
def test_website_staff_land_on_their_pages(db, role):
    user = make_user(f"{role}@example.test", staff_role=role, mfa=True, is_staff=True)
    response = login_client(user).get("/staff/")
    assert response.status_code == 302 and response["Location"] == reverse("staff-website-pages")


def test_other_staff_still_get_the_task_dashboard(db):
    ops = make_user("ops.landing@example.test", staff_role=StaffRole.OPERATIONS, mfa=True, is_staff=True)
    assert login_client(ops).get("/staff/").status_code == 200
    assert Client().get("/staff/").status_code == 302  # signed out: still asked to sign in


def test_website_staff_menu_shows_only_what_they_can_use(editor):
    body = login_client(editor).get("/staff/website/").content.decode()
    assert 'href="/staff/website/"' in body
    assert 'href="/staff/queue/"' not in body and ">Operations Queue<" not in body
