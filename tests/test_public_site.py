from __future__ import annotations

import re

import pytest
from django.template import Context, Template
from django.test import Client
from django.urls import reverse

from apps.catalogue.models import ServiceCategory

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


@pytest.mark.parametrize("name", ["landing", "contact"])
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
