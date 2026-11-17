"""Every workspace page (client and staff) uses the banda shell and colours."""
from __future__ import annotations

from .conftest import login_client

STAFF_PAGES = ("/staff/", "/staff/queue/", "/staff/my-cases/", "/staff/assignments/", "/staff/needs-information/",
               "/staff/scoping/", "/staff/reviews/", "/staff/deliverables/", "/staff/clients/", "/staff/support/",
               "/staff/finance/", "/staff/audit/", "/staff/catalogue/", "/staff/website/", "/staff/website/new/", "/staff/leads/",
               "/app/notifications/")


def test_staff_pages_use_only_the_banda_stylesheets(db):
    from .conftest import make_user

    admin = make_user("admin@example.test", staff_role="admin", mfa=True)
    c = login_client(admin)
    for path in STAFF_PAGES:
        response = c.get(path)
        assert response.status_code == 200, path
        body = response.content.decode()
        assert 'id="sidebar"' in body and "/static/banda/staff.css" in body, path
        assert "/static/css/app.css" not in body and "legacy" not in body, path


def test_page_titles_are_not_doubled(client, owner):
    body = client.get("/app/plan/").content.decode()
    title = body.split("<title>")[1].split("</title>")[0]
    assert title.count("B &amp; A Essentials") == 1


def test_banda_pages_keep_their_own_stylesheets(client, owner):
    body = client.get("/app/security/").content.decode()
    assert "/static/banda/app.css" in body and "/static/css/app.css" not in body


def test_redesigned_pages_use_only_the_banda_stylesheet(client, owner):
    for path in ("/app/", "/app/tasks/", "/app/files/", "/app/messages/", "/app/support/", "/app/support/new/",
                 "/app/plan/", "/app/billing/", "/app/catalogue/", "/app/notifications/", "/app/team/"):
        body = client.get(path).content.decode()
        assert "/static/banda/app.css" in body and "/static/css/app.css" not in body, path
