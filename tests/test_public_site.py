from __future__ import annotations

import re

import pytest
from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.test import Client
from django.urls import reverse

from apps.catalogue.models import ServiceCategory
from apps.website.models import Testimonial

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


@pytest.mark.parametrize("name", ["landing", "contact", "about"])
def test_public_pages_load_nothing_from_third_party_origins(name):
    """The CSP only allows same-origin assets, so templates must not reference CDNs."""
    content = Client().get(reverse(name)).content.decode()

    sources = re.findall(r'<(?:script|link|img)[^>]+(?:src|href)="([^"]+)"', content)
    assert sources
    assert all(source.startswith("/static/") for source in sources), sources
    assert "<script>" not in content


def test_landing_hides_testimonials_until_real_ones_are_published(settings):
    settings.DEBUG = False

    content = _landing()
    assert "What Our Clients Say" not in content
    assert "Ada Okafor" not in content

    Testimonial.objects.create(quote="Draft quote.", name="Draft Person", consent_confirmed=True)
    Testimonial.objects.create(quote="No consent.", name="Quiet Person", is_published=True)
    assert "What Our Clients Say" not in _landing()

    Testimonial.objects.create(
        quote="They sorted our invoices in two days.",
        name="Chidi Nwosu",
        role="Owner, Nwosu Stores",
        rating=4,
        consent_confirmed=True,
        is_published=True,
    )
    content = _landing()
    assert "What Our Clients Say" in content
    assert "They sorted our invoices in two days." in content
    assert "Chidi Nwosu" in content and ">CN<" in content
    assert "4 out of 5 stars" in content
    assert "Draft Person" not in content and "Quiet Person" not in content
    assert "Sample content" not in content


def test_landing_labels_sample_testimonials_in_debug(settings):
    settings.DEBUG = True

    content = _landing()

    assert "What Our Clients Say" in content
    assert "Sample content, shown in development only" in content


def test_testimonial_cannot_be_published_without_consent():
    testimonial = Testimonial(quote="Great.", name="Ada", is_published=True)

    with pytest.raises(ValidationError):
        testimonial.full_clean()


def test_about_page_is_public_and_linked_from_the_menu():
    response = Client().get(reverse("about"))

    assert response.status_code == 200
    assert "Beyond your AI." in response.content.decode()
    assert f'href="{reverse("about")}"' in _landing()


def test_published_about_page_replaces_the_built_in_copy(django_user_model):
    from apps.website import services as website_services

    author = django_user_model.objects.create_user(email="editor@example.test", password="x")
    page, revision = website_services.create_page(
        slug="about", title="Our story", summary="Summary.", body="Written by the team.", actor=author
    )
    assert "Beyond your AI." in Client().get(reverse("about")).content.decode()  # draft only

    website_services.publish(page, revision, actor=author)
    content = Client().get(reverse("about")).content.decode()
    assert "Written by the team." in content
    assert "Beyond your AI." not in content
    # The menu keeps a single About link rather than listing the page twice.
    assert _landing().count(">Our story<") == 0


def test_landing_uses_default_services_until_catalogue_is_loaded():
    assert "IT &amp; Digital Support" in _landing()

    ServiceCategory.objects.create(name="Bookkeeping Desk", slug="bookkeeping-desk", summary="Monthly books.")

    content = _landing()
    assert "Bookkeeping Desk" in content
    assert "IT &amp; Digital Support" not in content


def test_footer_social_icons_link_only_when_configured(settings):
    settings.SOCIAL_LINKS = {"linkedin": "", "x": ""}
    content = _landing()
    # All five icons are always shown, but an unconfigured one is not a link.
    assert content.count('role="img" aria-label=') >= 5
    assert '<a href="" ' not in content

    settings.SOCIAL_LINKS = {"linkedin": "https://example.test/company/banda", "x": ""}
    content = _landing()
    assert 'href="https://example.test/company/banda" rel="noopener noreferrer" aria-label="LinkedIn"' in content
    assert '<span role="img" aria-label="X"' in content


def test_icon_tag_escapes_class_and_ignores_unknown_names():
    template = Template('{% load site %}{% icon "check" css %}|{% icon "nope" %}')

    rendered = template.render(Context({"css": 'h-4"><script>'}))

    assert "<script>" not in rendered
    assert rendered.startswith("<svg") and rendered.endswith("|")
