"""What search engines and link previews read: robots.txt, the sitemap, canonical links, structured data."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client, override_settings

from apps.accounts.models import StaffRole
from apps.website import home, services
from apps.website.models import Page

from .conftest import login_client, make_user

PUBLIC = ["/", "/services/", "/contact/", "/terms/", "/privacy/", "/cookies/"]
STATIC = Path(__file__).resolve().parent.parent / "static"


def _base():
    return settings.APP_BASE_URL.rstrip("/")


def _ld(body: str) -> dict:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL)
    assert match, "no structured data"
    return json.loads(match.group(1))


@pytest.fixture
def editor(db):
    return make_user("seo.editor@example.test", staff_role=StaffRole.WEBSITE_EDITOR, mfa=True, is_staff=True)


@pytest.fixture
def publisher(db):
    return make_user("seo.publisher@example.test", staff_role=StaffRole.WEBSITE_PUBLISHER, mfa=True, is_staff=True)


# ---------- robots.txt and the sitemap ----------

def test_robots_txt_keeps_private_areas_out_and_points_to_the_sitemap(db):
    response = Client().get("/robots.txt")
    assert response.status_code == 200 and response["Content-Type"].startswith("text/plain")
    body = response.content.decode()
    for path in ("/app/", "/staff/", "/login/", "/step-up/", "/webhooks/"):
        assert f"Disallow: {path}" in body
    assert "Disallow: /\n" not in body and "/admin" not in body  # the site itself is open; the admin isn't advertised
    assert f"Sitemap: {_base()}/sitemap.xml" in body


def test_sitemap_lists_public_pages_and_nothing_else(editor, publisher):
    from apps.catalogue import guides

    page, revision = services.create_page(slug="about", title="About", summary="", body="x", actor=editor)
    services.publish(page, revision, actor=publisher)
    hidden, hidden_rev = services.create_page(slug="partner-offer", title="Offer", summary="", body="x",
                                              actor=editor, hide_from_search=True)
    services.publish(hidden, hidden_rev, actor=publisher)
    services.create_page(slug="draft-only", title="Draft", summary="", body="x", actor=editor)

    response = Client().get("/sitemap.xml")
    assert response["Content-Type"].startswith("application/xml")
    root = ET.fromstring(response.content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = [u.findtext("s:loc", namespaces=ns) for u in root.findall("s:url", ns)]
    base = _base()
    for path in PUBLIC + ["/about/"]:
        assert base + path in urls, path
    assert all(base + f"/services/?industry={g['tag']}" in urls for g in guides.industries())
    assert not any(x in u for u in urls for x in ("partner-offer", "draft-only", "/app/", "/staff/", "/login/"))
    about = next(u for u in root.findall("s:url", ns) if u.findtext("s:loc", namespaces=ns) == base + "/about/")
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", about.findtext("s:lastmod", namespaces=ns))


# ---------- canonical links and link previews ----------

@pytest.mark.parametrize("path", PUBLIC)
def test_every_public_page_is_ready_for_search_and_sharing(db, path):
    body = Client().get(path).content.decode()
    assert '<html lang="en-NG"' in body
    assert f'<link rel="canonical" href="{_base()}{path}" />' in body
    assert f'property="og:url" content="{_base()}{path}"' in body
    assert 'property="og:title"' in body and 'property="og:description"' in body
    assert f'property="og:image" content="{_base()}/static/banda/public/og-default.png"' in body
    assert body.count("<h1") == 1  # one main heading
    title = re.search(r"<title>(.*?)</title>", body, re.DOTALL).group(1)
    description = re.search(r'<meta name="description" content="(.*?)" />', body, re.DOTALL).group(1)
    assert 10 <= len(title) <= 70, title
    assert 50 <= len(re.sub(r"&\w+;", "&", description)) <= 160, (len(description), description)


@override_settings(ALLOWED_HOSTS=["*"])
def test_a_forged_host_header_cannot_change_canonical_links_or_the_sitemap(db):
    evil = Client(HTTP_HOST="evil.example")
    for path in ("/", "/services/", "/sitemap.xml", "/robots.txt"):
        assert "evil.example" not in evil.get(path).content.decode(), path


def test_filters_share_the_services_canonical_but_industry_guides_have_their_own(db):
    from apps.catalogue import guides

    body = Client().get("/services/?category=business-admin").content.decode()
    assert f'<link rel="canonical" href="{_base()}/services/" />' in body
    tag = guides.industries()[0]["tag"]
    body = Client().get(f"/services/?industry={tag}").content.decode()
    assert f'<link rel="canonical" href="{_base()}/services/?industry={tag}" />' in body


def test_error_pages_have_no_canonical_and_are_not_indexed(db):
    body = Client().get("/no-such-page/").content.decode()
    assert 'rel="canonical"' not in body and '<meta name="robots" content="noindex" />' in body


def test_private_areas_are_never_indexed(db, owner):
    assert login_client(owner).get("/app/")["X-Robots-Tag"] == "noindex, nofollow"


@override_settings(GOOGLE_SITE_VERIFICATION="abc123", BING_SITE_VERIFICATION="def456")
def test_search_console_verification_tags_when_set(db):
    body = Client().get("/").content.decode()
    assert '<meta name="google-site-verification" content="abc123" />' in body
    assert '<meta name="msvalidate.01" content="def456" />' in body


def test_no_verification_tags_by_default(db):
    body = Client().get("/").content.decode()
    assert "google-site-verification" not in body and "msvalidate" not in body


# ---------- structured data ----------

def test_home_page_describes_the_business_site_and_questions(db):
    graph = _ld(Client().get("/").content.decode())["@graph"]
    types = {node["@type"]: node for node in graph}
    assert set(types) == {"Organization", "ProfessionalService", "WebSite", "FAQPage"}
    assert types["Organization"]["url"] == _base() + "/" and types["Organization"]["logo"].endswith("icon-512.png")
    assert types["ProfessionalService"]["areaServed"] == {"@type": "Country", "name": "Nigeria"}
    assert "address" not in types["ProfessionalService"] and "telephone" not in types["ProfessionalService"]
    questions = types["FAQPage"]["mainEntity"]
    assert len(questions) == len(home.DEFAULT_FAQS)
    first = questions[0]["acceptedAnswer"]["text"]
    assert "browse every task" in first and "](" not in first and "**" not in first  # plain text, no formatting marks


@override_settings(BUSINESS_CITY="Lagos", BUSINESS_REGION="Lagos", BUSINESS_PHONE="+234 800 000 0000",
                   SEO_SAME_AS=["https://www.linkedin.com/company/example"])
def test_optional_business_details_appear_when_set(db):
    graph = _ld(Client().get("/").content.decode())["@graph"]
    business = next(n for n in graph if n["@type"] == "ProfessionalService")
    organization = next(n for n in graph if n["@type"] == "Organization")
    assert business["address"] == {"@type": "PostalAddress", "addressLocality": "Lagos", "addressCountry": "NG",
                                   "addressRegion": "Lagos"}
    assert business["telephone"] == "+234 800 000 0000"
    assert organization["sameAs"] == ["https://www.linkedin.com/company/example"]


def test_questions_follow_the_published_home_page_and_cannot_break_out_of_the_data_block(editor, publisher):
    attack = '</script><script>alert(1)</script>'
    content = home.defaults()
    content["faqs"] = [{"question": "Safe? " + attack, "answer": "**Yes** " + attack}]
    home.publish(home.save_draft(content, actor=editor), actor=publisher)
    body = Client().get("/").content.decode()
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL).group(1)
    assert "<" not in block and ">" not in block  # nothing in the data can close the script tag
    faq = next(n for n in _ld(body)["@graph"] if n["@type"] == "FAQPage")
    assert faq["mainEntity"][0]["name"] == "Safe? " + attack  # still the right text once read as JSON
    assert faq["mainEntity"][0]["acceptedAnswer"]["text"].startswith("Yes ")


def test_preview_has_no_structured_data(editor):
    revision = home.save_draft(home.defaults(), actor=editor)
    body = login_client(editor).get(f"/staff/website/home/{revision.pk}/preview/").content.decode()
    assert "application/ld+json" not in body


# ---------- icons ----------

def test_icons_exist_and_favicon_ico_points_to_one(db):
    for name in ("favicon.svg", "favicon-48.png", "favicon.ico", "apple-touch-icon.png", "icon-512.png",
                 "og-default.png"):
        assert (STATIC / "banda" / "public" / name).stat().st_size > 0, name
    response = Client().get("/favicon.ico")
    assert response.status_code == 301 and response["Location"].endswith("banda/public/favicon.ico")


def test_page_editor_pages_get_article_previews(editor, publisher):
    page, revision = services.create_page(slug="about", title="About us", summary="Who we are.", body="x",
                                          actor=editor)
    services.publish(page, revision, actor=publisher)
    body = Client().get("/about/").content.decode()
    assert 'property="og:type" content="article"' in body and 'property="og:title" content="About us"' in body
    assert body.count('rel="canonical"') == 1
    assert Page.objects.filter(slug="about").exists()
