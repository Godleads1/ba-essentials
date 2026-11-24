"""Outgoing provider calls must not use Python's default User-Agent.

Paystack's API is behind Cloudflare, which refuses "Python-urllib/x.y" with
error 1010 (HTTP 403). That broke every checkout on the live server, so each
client is checked here with the network replaced.
"""
import io
import json
from unittest import mock

import pytest

from apps.accounts import termii
from apps.billing import flutterwave, paystack
from apps.core.http import USER_AGENT

# conftest swaps each client's _request for a fake in every test. Keep the real
# ones (captured at import, before any fixture runs) so the outgoing request
# itself can be checked, with only urlopen replaced.
REAL_PAYSTACK = paystack._request
REAL_FLUTTERWAVE = flutterwave._request
REAL_TERMII = termii._request


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(body: dict):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["request"] = request
        return _Response(json.dumps(body).encode("utf-8"))

    return seen, fake_urlopen


def _user_agent(request):
    # urllib stores header names capitalised ("User-agent").
    return request.get_header("User-agent")


def test_user_agent_is_not_python_default():
    assert "python" not in USER_AGENT.lower()
    assert USER_AGENT.startswith("BandaEssentials/")


def test_paystack_sends_app_user_agent(settings):
    settings.PAYSTACK_SECRET_KEY = "sk_test_synthetic"
    seen, fake = _capture({"status": True, "data": {"total_transactions": 0}})
    with mock.patch("urllib.request.urlopen", fake):
        assert REAL_PAYSTACK("GET", "/transaction/totals") == {"total_transactions": 0}
    assert _user_agent(seen["request"]) == USER_AGENT


def test_flutterwave_sends_app_user_agent(settings):
    settings.FLUTTERWAVE_SECRET_KEY = "FLWSECK_TEST-synthetic"
    seen, fake = _capture({"status": "success", "data": {}})
    with mock.patch("urllib.request.urlopen", fake):
        try:
            REAL_FLUTTERWAVE("GET", "/transactions/1/verify")
        except flutterwave.FlutterwaveError:
            pass  # only the outgoing request matters here
    assert _user_agent(seen["request"]) == USER_AGENT


@pytest.mark.django_db
def test_termii_sends_app_user_agent(settings):
    settings.TERMII_BASE_URL = "https://api.example.test"
    settings.TERMII_API_KEY = "synthetic"
    seen, fake = _capture({"message_id": "1", "message": "Successfully Sent"})
    with mock.patch("urllib.request.urlopen", fake):
        try:
            REAL_TERMII({"to": "2348000000000", "sms": "hi"})
        except termii.TermiiError:
            pass
    assert _user_agent(seen["request"]) == USER_AGENT
