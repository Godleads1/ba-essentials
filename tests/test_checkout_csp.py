"""The checkout button posts to this site, which sends the browser on to the payment page.

Browsers apply the CSP form-action directive to that redirect as well (Chromium blocks it and the
client stays on the plan page), so every checkout host the billing code accepts must be allowed.
"""
from __future__ import annotations

import pytest
from django.test import Client

from apps.billing import flutterwave, paystack
from apps.core.checks import _form_action_sources, checkout_hosts_allowed_by_csp

CHECKOUT_HOSTS = sorted(paystack.CHECKOUT_HOSTS | flutterwave.CHECKOUT_HOSTS)


@pytest.mark.parametrize("host", CHECKOUT_HOSTS)
def test_every_checkout_host_is_allowed_in_form_action(db, host):
    policy = Client().get("/").headers["Content-Security-Policy"]
    assert f"https://{host}" in _form_action_sources(policy)


def test_form_action_still_refuses_other_sites(db):
    sources = _form_action_sources(Client().get("/").headers["Content-Security-Policy"])
    assert "*" not in sources and "https:" not in sources and "'self'" in sources


def test_scripts_are_still_this_site_only(db):
    policy = Client().get("/").headers["Content-Security-Policy"]
    assert "script-src 'self';" in policy and "frame-ancestors 'none'" in policy


def test_the_default_policy_passes_the_start_up_check():
    assert checkout_hosts_allowed_by_csp(None) == []


def test_an_override_without_the_checkout_hosts_is_flagged(settings):
    settings.CONTENT_SECURITY_POLICY = "default-src 'self'; form-action 'self'"
    (warning,) = checkout_hosts_allowed_by_csp(None)
    assert warning.id == "banda.W005" and "checkout.paystack.com" in warning.msg
