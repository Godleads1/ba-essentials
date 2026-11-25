"""Flutterwave's test checkout host is accepted only with a test key.

In test mode Flutterwave hands out checkout links on checkout-v2.dev-flutterwave.com,
which the live allowlist refused ("unexpected checkout address"), so no test payment
could start. A live key must never send clients there.
"""
from unittest import mock

import pytest

from apps.billing import flutterwave
from apps.core.checks import checkout_hosts_allowed_by_csp

TEST_LINK = "https://checkout-v2.dev-flutterwave.com/v3/hosted/pay/abc123"
LIVE_LINK = "https://checkout.flutterwave.com/v3/hosted/pay/abc123"

# conftest may swap the client for a fake; keep the real one, captured at import.
REAL_INITIALIZE = flutterwave.initialize


def _start(link):
    with mock.patch.object(flutterwave, "_request", return_value={"link": link}):
        return REAL_INITIALIZE(
            reference="BA-TEST", amount_minor=5500, currency="USD",
            email="client@example.test", callback_url="https://bandaessentials.com/r/", metadata={},
        )


def test_test_key_accepts_test_checkout(settings):
    settings.FLUTTERWAVE_SECRET_KEY = "FLWSECK_TEST-synthetic-X"
    assert _start(TEST_LINK) == TEST_LINK
    assert _start(LIVE_LINK) == LIVE_LINK


def test_live_key_refuses_test_checkout(settings):
    settings.FLUTTERWAVE_SECRET_KEY = "FLWSECK-synthetic-X"
    with pytest.raises(flutterwave.FlutterwaveError):
        _start(TEST_LINK)
    assert _start(LIVE_LINK) == LIVE_LINK


def test_other_hosts_are_refused_even_in_test_mode(settings):
    settings.FLUTTERWAVE_SECRET_KEY = "FLWSECK_TEST-synthetic-X"
    with pytest.raises(flutterwave.FlutterwaveError):
        _start("https://checkout-v2.dev-flutterwave.com.evil.test/pay")


def test_start_up_check_wants_the_test_host_in_form_action_with_a_test_key(settings):
    settings.FLUTTERWAVE_SECRET_KEY = "FLWSECK_TEST-synthetic-X"
    settings.CONTENT_SECURITY_POLICY = (
        "default-src 'self'; form-action 'self' https://checkout.paystack.com "
        "https://checkout.flutterwave.com https://checkout-v3.flutterwave.com"
    )
    (warning,) = checkout_hosts_allowed_by_csp(None)
    assert "checkout-v2.dev-flutterwave.com" in warning.msg
    settings.CONTENT_SECURITY_POLICY += " https://checkout-v2.dev-flutterwave.com"
    assert checkout_hosts_allowed_by_csp(None) == []


def test_settings_add_the_test_host_to_form_action_only_for_a_test_key(monkeypatch):
    import importlib

    from config.settings import base

    try:
        monkeypatch.setenv("FLUTTERWAVE_SECRET_KEY", "FLWSECK_TEST-synthetic-X")
        assert "https://checkout-v2.dev-flutterwave.com" in importlib.reload(base).CONTENT_SECURITY_POLICY
        monkeypatch.setenv("FLUTTERWAVE_SECRET_KEY", "FLWSECK-synthetic-X")
        assert "dev-flutterwave" not in importlib.reload(base).CONTENT_SECURITY_POLICY
    finally:
        monkeypatch.undo()
        importlib.reload(base)
