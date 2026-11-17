"""The founder's Service Catalogue v2.1 (the task bank), loaded with bootstrap_catalogue."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.urls import reverse

from apps.catalogue import services
from apps.catalogue.models import ServiceCategory, ServiceDefinition, ServiceVersion

MANIFEST = Path(settings.BASE_DIR) / "catalogue" / "service_catalogue_v2.1.json"


@pytest.fixture
def loaded(db):
    call_command("bootstrap_catalogue", str(MANIFEST), stdout=open("/dev/null", "w"))  # noqa: SIM115


def test_manifest_matches_the_sheet_summary():
    data = json.loads(MANIFEST.read_text())
    tasks = [s for c in data["categories"] for s in c["services"]]
    assert len(tasks) == 330
    assert len({t["code"] for t in tasks}) == 330
    states = [t["state"] for t in tasks]
    assert states.count("available") == 172  # Launch
    assert states.count("scoping_only") == 93 + 48  # Scoping only, and Quoted / referral
    assert states.count("unavailable") == 17  # Gated
    assert not any("note" in t for t in tasks), "staff notes stay out of the public repository"


def test_loading_creates_every_task_with_its_lane_and_weight(loaded):
    assert ServiceDefinition.objects.count() == 330
    assert services.submittable_versions().count() == 313  # everything but Gated

    review = ServiceVersion.objects.get(definition__code="ADM01.01", is_current=True)
    assert (review.allowance_lane, review.weight, review.state, review.requires_quote) == (
        "general", 1, "available", False)
    assert review.client_provides and review.size_rule
    assert review.definition.subtype == "Proposal & Document Support"
    assert "GEN" in review.definition.industries.split(",")

    legal = ServiceCategory.objects.get(slug="legal-ip-review")
    assert legal.uses_legal_allowance is True
    assert ServiceCategory.objects.get(slug="operations").minimum_plan_rank == 2
    assert ServiceCategory.objects.get(slug="specialized-add-ons").minimum_plan_rank == 3


def test_gated_tasks_cannot_be_requested(loaded):
    healthcare = ServiceVersion.objects.filter(definition__code__startswith="ADM07", is_current=True)
    assert healthcare.exists()
    assert not services.submittable_versions().filter(definition__code__startswith="ADM07").exists()


def test_loading_again_publishes_nothing_new(loaded):
    before = ServiceVersion.objects.count()
    call_command("bootstrap_catalogue", str(MANIFEST), stdout=open("/dev/null", "w"))  # noqa: SIM115
    assert ServiceVersion.objects.count() == before


def test_catalogue_page_shows_categories_creator_view_and_industry_filter(loaded, client):
    start = client.get(reverse("service-catalogue")).content.decode()
    assert "Business &amp; Admin" in start and "Creator Essentials" in start

    creator = client.get(reverse("service-catalogue"), {"view": "creator"})
    assert creator.context["versions"] and all(v.definition.creator for v in creator.context["versions"])

    property_tasks = client.get(reverse("service-catalogue"), {"category": "legal-ip-review", "industry": "PRO"})
    assert property_tasks.context["versions"]
    assert all("PRO" in v.definition.industries for v in property_tasks.context["versions"])

    by_code = client.get(reverse("service-catalogue"), {"q": "LEG03.01"}).content.decode()
    assert "CAC business name registration" in by_code


def test_guides_file_points_only_at_real_tasks():
    from apps.catalogue import guides

    data = json.loads(MANIFEST.read_text())
    codes = {s["code"] for c in data["categories"] for s in c["services"]}
    tags = [g["tag"] for g in guides.industries()]
    assert tags == ["GEN", "FIN", "VAS", "PRO", "OGE", "CRE", "HLT", "ECO"]
    for g in guides.industries():
        assert g["steps"] and all(code in codes for s in g["steps"] for code in s["codes"])
    assert all(code in codes for e in guides.creator_entries() for code in e["codes"])


def test_industry_guide_page_shows_the_steps_in_order(loaded, client):
    start = client.get(reverse("service-catalogue")).content.decode()
    assert "Browse by industry" in start and "?industry=FIN" in start

    page = client.get(reverse("service-catalogue"), {"industry": "FIN"})
    steps = page.context["guide_steps"]
    assert steps[0]["step"] == "Start here"
    assert all(v.definition.code for s in steps for v in s["versions"])
    body = page.content.decode()
    assert "Fintech and payments" in body and "Every task for fintech and payments" in body
    assert page.context["guide_categories"]


def test_healthcare_guide_says_its_tasks_are_not_open_yet(loaded, client):
    page = client.get(reverse("service-catalogue"), {"industry": "HLT"})
    assert "Healthcare tasks aren't open yet" in page.content.decode()
    assert any(s["closed"] for s in page.context["guide_steps"])


def test_creator_view_follows_the_entry_points(loaded, client):
    page = client.get(reverse("service-catalogue"), {"view": "creator"})
    entries = [g["entry"] for g in page.context["creator_groups"]]
    assert entries[0] == "Contract and document filing"
    assert "Expense tracking and invoicing" in entries
