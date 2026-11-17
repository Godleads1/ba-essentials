"""The public Service Catalogue (/services/) and the landing page links to it."""
from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.urls import reverse

MANIFEST = Path(settings.BASE_DIR) / "catalogue" / "service_catalogue_v2.1.json"


@pytest.fixture
def visitor():
    from django.test import Client

    return Client()


@pytest.fixture
def loaded(db):
    call_command("bootstrap_catalogue", str(MANIFEST), stdout=open("/dev/null", "w"))  # noqa: SIM115


def test_visitors_can_browse_categories_and_tasks(loaded, visitor):
    start = visitor.get(reverse("public-services"))
    assert start.status_code == 200
    body = start.content.decode()
    assert "313 tasks" in body and "Business &amp; Admin" in body and "Browse by industry" in body
    assert "In every plan" in body and "From " in body

    page = visitor.get(reverse("public-services"), {"category": "business-admin"})
    body = page.content.decode()
    assert "Uses 1 task" in body and "Get started" in body
    assert "Request this" not in body  # visitors sign up first; no request form is linked


def test_visitors_see_industry_guides_and_search(loaded, visitor):
    guide = visitor.get(reverse("public-services"), {"industry": "FIN"})
    assert guide.context["guide_steps"][0]["step"] == "Start here"
    found = visitor.get(reverse("public-services"), {"q": "LEG03.01"}).content.decode()
    assert "CAC business name registration" in found


def test_gated_tasks_stay_off_the_public_page(loaded, visitor):
    page = visitor.get(reverse("public-services"), {"q": "ADM07"})
    assert page.context["versions"] == []


def test_signed_in_clients_go_to_the_catalogue_in_the_app(loaded, client, owner):
    client.force_login(owner)
    response = client.get(reverse("public-services"), {"category": "operations"})
    assert response.status_code == 302
    assert response["Location"] == reverse("service-catalogue") + "?category=operations"


def test_landing_links_each_category_to_its_tasks(loaded, visitor):
    body = visitor.get(reverse("landing")).content.decode()
    assert '/services/?category=legal-ip-review' in body and "/services/?view=creator" in body
    assert "Browse all 313 services" in body and "/services/?industry=FIN" in body
    assert "How are tasks counted?" in body


def test_landing_without_a_catalogue_still_renders(db, visitor):
    body = visitor.get(reverse("landing")).content.decode()
    assert "Browse services" in body and "See 0 tasks" not in body


def test_services_is_a_reserved_page_address():
    from apps.website.services import RESERVED_SLUGS

    assert "services" in RESERVED_SLUGS
