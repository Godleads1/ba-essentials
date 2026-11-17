"""Regression tests for the first security audit (17 Nov 2026): each repeats the original attack."""
from __future__ import annotations

import pytest
from django.core.checks import run_checks
from django.test import Client, RequestFactory, override_settings

from apps.core.client_ip import client_ip

from .conftest import login_client, make_user

EVIL = ["https://evil.example/phish", "//evil.example/x", "http://evil.example", "https:evil.example",
        "/\\evil.example", "javascript:alert(1)"]


# ---------- F1: no redirect to another website ----------

@pytest.mark.parametrize("target", EVIL)
def test_sign_in_never_leads_off_site(target, owner, monkeypatch):
    from apps.accounts import codes

    monkeypatch.setattr(codes, "verify_sign_in", lambda pending, code, client_ip="": owner)
    c = Client()
    c.post(f"/login/?next={target}", {"identifier": owner.email})
    response = c.post("/login/code/", {"code": "123456"})
    assert response.status_code == 302 and response["Location"] == "/app/"


def test_sign_in_still_returns_to_a_page_on_this_site(owner, monkeypatch):
    from apps.accounts import codes

    monkeypatch.setattr(codes, "verify_sign_in", lambda pending, code, client_ip="": owner)
    c = Client()
    c.post("/login/?next=/app/billing/", {"identifier": owner.email})
    assert c.post("/login/code/", {"code": "123456"})["Location"] == "/app/billing/"


@pytest.mark.parametrize("target", EVIL)
def test_confirm_its_you_never_leads_off_site(target, owner, monkeypatch):
    from apps.accounts import codes, services

    monkeypatch.setattr(codes, "verify_step_up_code", lambda user, code: True)
    monkeypatch.setattr(services, "verify_step_up_password", lambda user, password: True)
    response = login_client(owner).post(f"/step-up/?next={target}", {"code": "123456", "password": "x"})
    assert response.status_code == 302 and response["Location"] == "/app/"


def test_confirm_its_you_returns_to_the_page_asked_for(owner, monkeypatch):
    from apps.accounts import codes, services

    monkeypatch.setattr(codes, "verify_step_up_code", lambda user, code: True)
    monkeypatch.setattr(services, "verify_step_up_password", lambda user, password: True)
    response = login_client(owner).post("/step-up/?next=/app/security/", {"code": "123456", "password": "x"})
    assert response["Location"] == "/app/security/"


@pytest.mark.parametrize("target", EVIL)
def test_staff_sign_in_never_leads_off_site(target, db):
    from .conftest import PASSWORD

    staff = make_user("staff.redirect@example.test", staff_role="operations")
    response = Client().post(f"/login/staff/?next={target}", {"email": staff.email, "password": PASSWORD})
    assert response.status_code == 302 and response["Location"] == "/app/"


def test_staff_sign_in_still_returns_to_a_page_on_this_site(db):
    from .conftest import PASSWORD

    staff = make_user("staff.control@example.test", staff_role="operations")
    response = Client().post("/login/staff/?next=/staff/", {"email": staff.email, "password": PASSWORD})
    assert response.status_code == 302 and response["Location"] == "/staff/"


@pytest.mark.parametrize("target", EVIL)
def test_staff_with_an_authenticator_never_leads_off_site(target, db, monkeypatch):
    """Real staff have a second factor: password, then code, then the redirect."""
    from apps.accounts import services

    from .conftest import PASSWORD

    staff = make_user("staff.mfa@example.test", staff_role="operations", mfa=True)
    monkeypatch.setattr(services, "verify_mfa", lambda user, code: True)
    c = Client()
    first = c.post(f"/login/staff/?next={target}", {"email": staff.email, "password": PASSWORD})
    assert first["Location"] == "/login/mfa/"
    response = c.post("/login/mfa/", {"code": "123456"})
    assert response.status_code == 302 and response["Location"] == "/app/"


# ---------- F2: the visitor's address can't be forged ----------

def _request(remote="203.0.113.9", forwarded=None, **meta):
    extra = {"REMOTE_ADDR": remote, **meta}
    if forwarded is not None:
        extra["HTTP_X_FORWARDED_FOR"] = forwarded
    return RequestFactory().get("/", **extra)


@override_settings(CLIENT_IP_HEADER="", TRUSTED_PROXY_COUNT=0)
def test_forwarded_for_is_ignored_unless_a_proxy_is_trusted():
    assert client_ip(_request(forwarded="10.9.9.9")) == "203.0.113.9"


@override_settings(CLIENT_IP_HEADER="", TRUSTED_PROXY_COUNT=1)
def test_with_one_proxy_the_address_it_added_is_used_not_the_visitors_claim():
    # The visitor sent "1.1.1.1"; our proxy appended the real address it saw.
    assert client_ip(_request(remote="10.0.0.2", forwarded="1.1.1.1, 198.51.100.7")) == "198.51.100.7"


@override_settings(CLIENT_IP_HEADER="", TRUSTED_PROXY_COUNT=2)
def test_with_two_proxies_the_second_from_the_right_is_used():
    assert client_ip(_request(remote="10.0.0.2", forwarded="1.1.1.1, 198.51.100.7, 10.0.0.1")) == "198.51.100.7"


@override_settings(CLIENT_IP_HEADER="HTTP_CF_CONNECTING_IP", TRUSTED_PROXY_COUNT=0)
def test_edge_header_is_used_when_configured():
    assert client_ip(_request(forwarded="1.1.1.1", HTTP_CF_CONNECTING_IP="198.51.100.8")) == "198.51.100.8"


@override_settings(CLIENT_IP_HEADER="HTTP_CF_CONNECTING_IP", TRUSTED_PROXY_COUNT=1)
def test_junk_in_the_headers_falls_back_safely():
    assert client_ip(_request(remote="10.0.0.2", forwarded="not-an-ip", HTTP_CF_CONNECTING_IP="<script>")) == "10.0.0.2"


@override_settings(CLIENT_IP_HEADER="", TRUSTED_PROXY_COUNT=0)
def test_forging_the_header_no_longer_beats_the_per_ip_code_limit(db, fake_termii):
    from apps.accounts import codes

    sent = 0
    for i in range(codes.SENDS_PER_IP_PER_HOUR + 5):
        make_user(f"target{i}@example.test")
        r = Client(HTTP_X_FORWARDED_FOR=f"10.0.{i}.1").post("/login/", {"identifier": f"target{i}@example.test"})
        sent += r.status_code == 302 and r["Location"].endswith("/login/code/")
    assert sent == codes.SENDS_PER_IP_PER_HOUR


@override_settings(CLIENT_IP_HEADER="", TRUSTED_PROXY_COUNT=0)
def test_deploy_check_warns_when_no_proxy_setting_is_given():
    assert "banda.W001" in [m.id for m in run_checks(include_deployment_checks=True)]


@override_settings(TRUSTED_PROXY_COUNT=1)
def test_deploy_check_is_quiet_once_configured():
    assert "banda.W001" not in [m.id for m in run_checks(include_deployment_checks=True)]
