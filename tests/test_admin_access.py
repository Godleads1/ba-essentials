"""Django admin must be as hard to reach as the staff app.

Covers audit findings F-01 (admin skipped the staff second factor) and F-03 (the
built-in admin sign-in checked passwords without rate limits or lockout).
"""
from __future__ import annotations

import pytest
from django.test import Client

from apps.accounts.models import StaffRole, User

from .conftest import PASSWORD, login_client, make_user

ADMIN_PAGES = ["/admin/", "/admin/accounts/user/", "/admin/tasks/task/", "/admin/audit/auditevent/"]


def _mfa_redirect(response) -> bool:
    return response.status_code == 302 and response["Location"].startswith("/app/security/mfa")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role, superuser",
    [
        (StaffRole.OPERATIONS, False),  # staff-app role with admin access
        ("", False),  # admin-only account, no staff-app role
        (StaffRole.ADMIN, True),  # full superuser
    ],
)
def test_admin_requires_second_factor(role, superuser):
    user = make_user(
        f"no-mfa-{role or 'none'}@example.test",
        staff_role=role,
        mfa=False,
        is_staff=True,
        is_superuser=superuser,
    )
    client = login_client(user)
    for page in ADMIN_PAGES:
        response = client.get(page)
        assert _mfa_redirect(response), f"{page} answered {response.status_code} without a second factor"


@pytest.mark.django_db
def test_admin_open_after_second_factor():
    user = make_user(
        "admin-mfa@example.test",
        staff_role=StaffRole.ADMIN,
        mfa=True,
        is_staff=True,
        is_superuser=True,
    )
    client = login_client(user)
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/accounts/user/").status_code == 200


@pytest.mark.django_db
def test_client_accounts_are_not_asked_for_a_second_factor(owner):
    # Clients are not staff; the gate must not lock them out of their own app.
    response = login_client(owner).get("/app/")
    assert not _mfa_redirect(response)


@pytest.mark.django_db
def test_admin_sign_in_form_is_replaced_by_app_sign_in():
    response = Client().get("/admin/login/?next=/admin/accounts/user/")
    assert response.status_code == 302
    assert response["Location"] == "/login/?next=%2Fadmin%2Faccounts%2Fuser%2F"


@pytest.mark.django_db
@pytest.mark.parametrize("unsafe", ["https://evil.example/", "//evil.example/admin/", "/app/billing/", ""])
def test_admin_sign_in_keeps_only_admin_destinations(unsafe):
    response = Client().get("/admin/login/", {"next": unsafe})
    assert response["Location"] == "/login/?next=%2Fadmin%2F"


@pytest.mark.django_db
def test_admin_sign_in_never_checks_a_password():
    user = make_user("guess-me@example.test", staff_role=StaffRole.ADMIN, mfa=True, is_staff=True)
    client = Client()
    answers = set()
    for password in ["wrong-1", "wrong-2", PASSWORD]:
        response = client.post("/admin/login/", {"username": user.email, "password": password})
        answers.add((response.status_code, response.get("Location")))
    # Right and wrong passwords get the same answer, so the form can't confirm a guess.
    assert answers == {(302, "/login/?next=%2Fadmin%2F")}
    # Nothing signed in, and the attempts didn't touch the account.
    assert "_auth_user_id" not in client.session
    user.refresh_from_db()
    assert user.failed_login_count == 0
    assert User.objects.get(pk=user.pk).locked_until is None
