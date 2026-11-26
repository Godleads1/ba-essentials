"""The legal pages name the right party and the right contact address for each job."""
import re

import pytest

pytestmark = pytest.mark.django_db


def _text(client, path):
    return re.sub(r"<[^>]+>", "", client.get(path).content.decode())


def test_disputes_and_complaints_go_to_complaints(client):
    terms = _text(client, "/terms/")
    assert "If you think a charge is wrong, contact complaints@bandaessentials.com" in terms
    assert "To make a complaint, use the task's support route or email complaints@bandaessentials.com" in terms
    for address in ("support", "complaints", "refunds", "dpo", "counselingunit"):
        assert f"{address}@bandaessentials.com" in terms


def test_privacy_names_the_business_and_has_no_placeholders(client):
    privacy = _text(client, "/privacy/")
    assert "[registered name" not in privacy and "7410773" not in privacy.replace("74100773", "")
    assert "B&amp;A Essential Services" in privacy and "BN 74100773" in privacy
    assert "complaints@bandaessentials.com" in privacy and "refunds@bandaessentials.com" in privacy


def test_no_limited_company_claim_while_a_business_name(client):
    for path in ("/terms/", "/privacy/"):
        page = _text(client, path)
        assert " Ltd" not in page and "Limited" not in page.replace("limited licence", "")
        assert "RC " not in page


def test_incorporation_is_one_settings_change(client, settings):
    settings.LEGAL_ENTITY_NAME = "B&A Essentials Ltd"
    settings.LEGAL_ENTITY_DESCRIPTION = "a private company limited by shares, registered in Nigeria under RC 1234567"
    terms = _text(client, "/terms/")
    assert "contract between you and B&amp;A Essentials Ltd, a private company limited by shares" in terms
    assert "RC 1234567" in _text(client, "/privacy/")
