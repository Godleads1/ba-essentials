from __future__ import annotations

import re

import pytest
from django.template import Context, Template
from django.test import Client
from django.urls import reverse

from apps.catalogue import services as catalogue_services
from apps.catalogue.models import ServiceCategory, ServiceDefinition, ServiceState

pytestmark = pytest.mark.django_db


def _landing(**kwargs) -> str:
    response = Client().get(reverse("landing"), **kwargs)
    assert response.status_code == 200
    return response.content.decode()


def test_landing_shows_banda_brand_and_real_routes():
    content = _landing()

    assert 'class="logo-word"' in content
    assert "Get More Done with" in content
    assert f'href="{reverse("signup")}"' in content
    assert f'href="{reverse("login")}"' in content
    assert f'href="{reverse("contact")}"' in content
    # No placeholder links ship on the public page.
    assert 'href="#"' not in content


@pytest.mark.parametrize("name", ["landing", "contact", "services"])
def test_public_pages_load_nothing_from_third_party_origins(name):
    """The CSP only allows same-origin assets, so templates must not reference CDNs."""
    content = Client().get(reverse(name)).content.decode()

    sources = re.findall(r'<(?:script|link|img)[^>]+(?:src|href)="([^"]+)"', content)
    assert sources
    assert all(source.startswith("/static/") for source in sources), sources
    assert "<script>" not in content


def test_landing_hides_sample_testimonials_outside_debug(settings):
    settings.DEBUG = False

    content = _landing()

    assert "What Our Clients Say" not in content
    assert "Ada Okafor" not in content


def test_landing_labels_sample_testimonials_in_debug(settings):
    settings.DEBUG = True

    content = _landing()

    assert "What Our Clients Say" in content
    assert "Sample content, shown in development only" in content


def test_landing_uses_default_services_until_catalogue_is_loaded():
    assert "IT &amp; Digital Support" in _landing()

    ServiceCategory.objects.create(name="Bookkeeping Desk", slug="bookkeeping-desk", summary="Monthly books.")

    content = _landing()
    assert "Bookkeeping Desk" in content
    assert "IT &amp; Digital Support" not in content


def test_footer_social_icons_follow_settings(settings):
    settings.SOCIAL_LINKS = {"linkedin": "", "x": ""}
    assert 'aria-label="LinkedIn"' not in _landing()

    settings.SOCIAL_LINKS = {"linkedin": "https://example.test/company/banda", "x": ""}
    content = _landing()
    assert 'href="https://example.test/company/banda"' in content
    assert 'aria-label="LinkedIn"' in content
    assert 'aria-label="X"' not in content


def test_icon_tag_escapes_class_and_ignores_unknown_names():
    template = Template('{% load site %}{% icon "check" css %}|{% icon "nope" %}')

    rendered = template.render(Context({"css": 'h-4"><script>'}))

    assert "<script>" not in rendered
    assert rendered.startswith("<svg") and rendered.endswith("|")


def _service(category, name, slug, *, state=ServiceState.AVAILABLE, scope="Scope text.", days=7):
    definition = ServiceDefinition.objects.create(category=category, name=name, slug=slug)
    return catalogue_services.publish_version(
        definition,
        scope_summary=scope,
        deliverables_summary="Deliverables text.",
        turnaround_days=days,
        state=state,
    )


@pytest.fixture
def catalogue():
    docs = ServiceCategory.objects.create(name="Business Documents", slug="business-documents", sort_order=1)
    design = ServiceCategory.objects.create(name="Content & Design", slug="content-design", sort_order=2)
    _service(docs, "Proposal Document", "proposal-document", scope="A structured business proposal.", days=9)
    _service(design, "Brand Style Guide", "brand-style-guide", state=ServiceState.SCOPING_ONLY, days=3)
    _service(design, "Retired Poster Pack", "retired-poster-pack", state=ServiceState.UNAVAILABLE)
    return docs, design


def _services(query: str = "") -> str:
    response = Client().get(reverse("services") + query)
    assert response.status_code == 200
    return response.content.decode()


def test_services_page_without_catalogue_invites_contact():
    content = _services()

    assert "Our service list is being finalised" in content
    assert 'name="q"' not in content


def test_services_page_lists_only_requestable_services(catalogue):
    content = _services()

    assert "Proposal Document" in content
    assert "Brand Style Guide" in content
    assert "Scoping first" in content
    assert "Retired Poster Pack" not in content
    assert reverse("service-detail", args=["proposal-document"]) in content
    # Prices are quoted per request, so the public page never shows a figure.
    assert "\u20a6" not in content


def test_services_page_filters_by_category_and_search(catalogue):
    by_category = _services("?category=content-design")
    assert "Brand Style Guide" in by_category
    assert "Proposal Document" not in by_category

    by_search = _services("?q=structured")
    assert "Proposal Document" in by_search
    assert "Brand Style Guide" not in by_search

    no_match = _services("?q=zzzz")
    assert "No services match your search" in no_match

    # Unknown filter values fall back to the full list instead of erroring.
    fallback = _services("?category=nope&sort=drop-table")
    assert "Proposal Document" in fallback and "Brand Style Guide" in fallback


def test_services_page_sorts_by_turnaround(catalogue):
    content = _services("?sort=turnaround")

    assert content.index("Brand Style Guide</a>") < content.index("Proposal Document</a>")


def test_services_search_term_is_escaped(catalogue):
    content = _services("?q=%3Cscript%3Ealert(1)%3C/script%3E")

    assert "<script>alert(1)" not in content


def test_service_detail_shows_scope_and_hides_unavailable(catalogue):
    public = Client()

    detail = public.get(reverse("service-detail", args=["proposal-document"]))
    assert detail.status_code == 200
    content = detail.content.decode()
    assert "A structured business proposal." in content
    assert "About 9 days" in content

    assert public.get(reverse("service-detail", args=["retired-poster-pack"])).status_code == 404
    assert public.get(reverse("service-detail", args=["missing"])).status_code == 404
