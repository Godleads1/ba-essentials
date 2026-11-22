"""What search engines read: robots.txt, the sitemap and structured data (schema.org JSON-LD).

Every address is built from settings.APP_BASE_URL, never from the request's Host
header, so a forged header can't change canonical links or the sitemap.
"""
from __future__ import annotations

import json
import re

from django.conf import settings
from django.http import HttpResponse, HttpResponsePermanentRedirect
from django.templatetags.static import static
from django.urls import reverse
from django.utils.html import escape
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

SITE_NAME = "banda — B & A Essentials"
BUSINESS_NAME = "B & A Essentials"
# Paths that are never useful in search results. /app/ and /staff/ also send noindex.
PRIVATE_PATHS = ["/app/", "/staff/", "/login/", "/step-up/", "/webhooks/"]


def site_url() -> str:
    return settings.APP_BASE_URL.rstrip("/")


def absolute(path: str) -> str:
    return site_url() + path


# ---------- robots.txt and sitemap.xml ----------

@require_GET
@cache_control(public=True, max_age=3600)
def robots_txt(request):
    lines = ["User-agent: *", *[f"Disallow: {p}" for p in PRIVATE_PATHS], "",
             f"Sitemap: {absolute('/sitemap.xml')}", ""]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


def sitemap_entries() -> list[tuple[str, object]]:
    """(absolute URL, last-modified datetime or None) for every public page worth indexing."""
    from apps.catalogue import guides

    from .models import HomePage, Page

    state = HomePage.objects.select_related("published_revision").filter(pk=1).first()
    home_changed = state.published_revision.created_at if state and state.published_revision else None
    entries = [(absolute("/"), home_changed), (absolute(reverse("public-services")), None)]
    entries += [(absolute(f"{reverse('public-services')}?industry={g['tag']}"), None) for g in guides.industries()]
    entries += [(absolute(reverse(name)), None) for name in ("contact", "terms", "privacy", "cookies")]
    pages = (Page.objects.select_related("published_revision")
             .filter(published_revision__isnull=False, published_revision__hide_from_search=False).order_by("slug"))
    entries += [(absolute(reverse("website-page", args=[p.slug])), p.published_revision.created_at) for p in pages]
    return entries


@require_GET
@cache_control(public=True, max_age=3600)
def sitemap_xml(request):
    rows = []
    for url, changed in sitemap_entries():
        lastmod = f"<lastmod>{changed.date().isoformat()}</lastmod>" if changed else ""
        rows.append(f"  <url><loc>{escape(url)}</loc>{lastmod}</url>")
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(rows) + "\n</urlset>\n")
    return HttpResponse(body, content_type="application/xml; charset=utf-8")


# ---------- structured data ----------

def plain(text: str) -> str:
    """Page-editor formatting turned into plain text: [label](url) -> label, no ** or *."""
    text = re.sub(r"\[([^\[\]\n]+)\]\([^()\s]+\)", r"\1", text or "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<![*\w])\*(\S[^*\n]*?)\*(?![*\w])", r"\1", text)
    return " ".join(text.split())


def home_structured_data(home, categories) -> list[dict]:
    """The business, the website and the home page questions, for Google's knowledge panel and results."""
    root = absolute("/")
    organization = {
        "@type": "Organization", "@id": root + "#organization", "name": BUSINESS_NAME, "alternateName": "banda",
        "url": root, "logo": absolute(static("banda/public/icon-512.png")), "email": settings.SUPPORT_EMAIL,
    }
    if settings.SEO_SAME_AS:
        organization["sameAs"] = list(settings.SEO_SAME_AS)
    business = {
        "@type": "ProfessionalService", "@id": root + "#business", "name": BUSINESS_NAME, "url": root,
        "description": plain(home.plain("seo_description")), "image": absolute(static("banda/public/og-default.png")),
        "email": settings.SUPPORT_EMAIL, "areaServed": {"@type": "Country", "name": "Nigeria"},
        "parentOrganization": {"@id": root + "#organization"},
        "knowsAbout": [c.name for c in categories],
    }
    if settings.BUSINESS_PHONE:
        business["telephone"] = settings.BUSINESS_PHONE
    if settings.BUSINESS_CITY:
        business["address"] = {"@type": "PostalAddress", "addressLocality": settings.BUSINESS_CITY,
                               "addressCountry": "NG",
                               **({"addressRegion": settings.BUSINESS_REGION} if settings.BUSINESS_REGION else {})}
    website = {"@type": "WebSite", "@id": root + "#website", "url": root, "name": SITE_NAME,
               "inLanguage": "en-NG", "publisher": {"@id": root + "#organization"}}
    graph = [organization, business, website]
    faqs = [f for f in home.faqs if f["question_text"]]
    if faqs:
        graph.append({"@type": "FAQPage", "@id": root + "#faq", "mainEntity": [
            {"@type": "Question", "name": plain(f["question_text"]),
             "acceptedAnswer": {"@type": "Answer", "text": plain(f["answer_text"])}} for f in faqs]})
    return graph


def ld_json(graph: list[dict]) -> str:
    """A <script type="application/ld+json"> block. <, > and & are escaped so no text can close the script."""
    data = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script type="application/ld+json">{data}</script>'


@require_GET
def favicon_ico(request):
    """Browsers and Google ask for /favicon.ico at the site root; send them to the real file."""
    return HttpResponsePermanentRedirect(static("banda/public/favicon.ico"))
