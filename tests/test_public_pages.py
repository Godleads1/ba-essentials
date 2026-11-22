"""The public site in the banda design: landing, legal pages, contact, website pages and errors."""
from __future__ import annotations

import re

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.website.models import ContactMessage

PUBLIC = ("/", "/terms/", "/privacy/", "/cookies/", "/contact/")


@pytest.fixture
def plans(db):
    call_command("seed_plans", stdout=open("/dev/null", "w"))  # noqa: SIM115


def _scripts_are_files_or_data(body: str) -> None:
    """The CSP allows scripts from this site only, so no inline code. JSON data blocks never run."""
    for tag in re.findall(r"<script[^>]*>", body):
        assert "src=" in tag or tag in ('<script type="application/ld+json">',) or 'type="application/json"' in tag, tag


@pytest.mark.parametrize("path", PUBLIC)
def test_public_pages_use_the_banda_design_and_no_outside_hosts(db, path):
    response = Client().get(path)
    assert response.status_code == 200
    body = response.content.decode()
    assert "/static/banda/app.css" in body and "/static/css/app.css" not in body
    assert "logo-word" in body
    _scripts_are_files_or_data(body)
    for host in ("fonts.googleapis.com", "unpkg.com", "cdnjs.cloudflare.com"):
        assert host not in body


def test_landing_prices_come_from_the_plans(plans):
    from apps.billing.models import Plan

    Plan.objects.filter(slug="starter").update(price_minor=7_000_000)
    body = Client().get("/").content.decode()
    assert 'data-price="starter">₦70,000<' in body
    assert 'id="pricingData"' in body


def test_landing_has_no_made_up_testimonials_or_dead_links(db):
    body = Client().get("/").content.decode()
    assert "Ada Okafor" not in body and "What Our Clients Say" not in body
    assert 'href="#"' not in body


def test_landing_sends_signed_in_people_to_their_workspace(client):
    assert client.get("/").status_code == 302


def test_legal_pages_carry_their_documents(db):
    assert "Terms of Service" in Client().get(reverse("terms")).content.decode()
    assert "Privacy Policy" in Client().get(reverse("privacy")).content.decode()
    assert "Cookie Notice" in Client().get(reverse("cookies")).content.decode()


def test_contact_form_saves_a_message_and_thanks_you(db):
    c = Client()
    response = c.post(reverse("contact"), {"name": "Synthetic Person", "email": "synthetic@example.test",
                                           "message": "Synthetic question about plans."})
    assert response.status_code == 302 and response["Location"].endswith("?sent=1")
    assert ContactMessage.objects.count() == 1
    assert "your message is with us" in c.get(response["Location"]).content.decode()


def test_contact_form_shows_errors_next_to_fields(db):
    body = Client().post(reverse("contact"), {"name": "", "email": "not-an-email", "message": ""}).content.decode()
    assert 'aria-invalid="true"' in body
    assert ContactMessage.objects.count() == 0


def test_contact_honeypot_saves_nothing(db):
    Client().post(reverse("contact"), {"name": "Bot", "email": "bot@example.test", "message": "Spam spam spam",
                                       "website": "http://spam.example"})
    assert ContactMessage.objects.count() == 0


def test_error_pages_use_the_banda_design(client):
    import uuid

    response = client.get(reverse("task-detail", args=[uuid.uuid4()]))
    assert response.status_code == 404
    body = response.content.decode()
    assert "logo-word" in body and "Page not found" in body


def test_sign_up_links_the_terms_and_privacy_policy(db):
    body = Client().get(reverse("signup")).content.decode()
    assert f'href="{reverse("terms")}"' in body and f'href="{reverse("privacy")}"' in body
