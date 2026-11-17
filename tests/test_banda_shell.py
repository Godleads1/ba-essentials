"""Every workspace page (client and staff) uses the banda shell and colours."""
from __future__ import annotations

from pathlib import Path

from django.conf import settings

from .conftest import login_client

LEGACY_CSS = ("banda/legacy-layers.css", "css/app.css", "banda/shell.css", "banda/legacy.css")


def _uses_banda_shell(body: str) -> None:
    assert 'id="sidebar"' in body and "logo-word" in body  # banda sidebar and logo
    positions = [body.index(f"/static/{name}") for name in LEGACY_CSS]
    assert positions == sorted(positions), "the cascade-order file must load before app.css"
    assert 'class="legacy-title"' in body
    assert body.count("<main") == 1


def test_staff_notifications_keep_the_legacy_wrapper_for_now(ops_user):
    _uses_banda_shell(login_client(ops_user).get("/app/notifications/").content.decode())


def test_staff_pages_use_the_banda_shell(ops_user):
    c = login_client(ops_user)
    for path in ("/staff/", "/staff/queue/"):
        _uses_banda_shell(c.get(path).content.decode())


def test_page_titles_are_not_doubled(client, owner):
    body = client.get("/app/plan/").content.decode()
    title = body.split("<title>")[1].split("</title>")[0]
    assert title.count("B &amp; A Essentials") == 1


def test_banda_pages_keep_their_own_stylesheets(client, owner):
    body = client.get("/app/security/").content.decode()
    assert "/static/banda/app.css" in body and "/static/css/app.css" not in body


def test_banda_utilities_are_the_preview_stylesheet_minus_its_reset():
    base = Path(settings.BASE_DIR) / "static" / "banda"
    full = (base / "app.css").read_text()
    cut = (base / "shell-utilities.css").read_text()
    header, body = cut.split("*/\n", 1)
    start, end = (int(x) for x in header.split("characters ")[1].split(" ")[0].split("-"))
    assert body == full[:start] + full[end:]
    assert "border:0 solid #e5e7eb" not in body


def test_redesigned_pages_use_only_the_banda_stylesheet(client, owner):
    for path in ("/app/", "/app/tasks/", "/app/files/", "/app/messages/", "/app/support/", "/app/support/new/",
                 "/app/plan/", "/app/billing/", "/app/catalogue/", "/app/notifications/", "/app/team/"):
        body = client.get(path).content.decode()
        assert "/static/banda/app.css" in body and "/static/css/app.css" not in body, path
