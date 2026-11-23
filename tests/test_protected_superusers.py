"""Protected superusers (apps/accounts/protection.py): never deleted, demoted or switched off.

The emails here are synthetic. Real ones live only in the server's PROTECTED_SUPERUSER_EMAILS.
"""
from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from apps.accounts import team
from apps.accounts.models import StaffRole, User
from apps.accounts.protection import ProtectedAccount, is_protected
from apps.core.exceptions import PermissionFailure

from .conftest import PASSWORD, login_client

FIRST, SECOND, THIRD = "first.owner@example.test", "second.owner@example.test", "third.admin@example.test"
pytestmark = pytest.mark.usefixtures("protected")


@pytest.fixture
def protected(settings):
    settings.PROTECTED_SUPERUSER_EMAILS = [FIRST, SECOND]


@pytest.fixture
def owners(db):
    return [User.objects.create_superuser(e, PASSWORD, full_name=e.split("@")[0], mfa_enabled=True)
            for e in (FIRST, SECOND, THIRD)]


def test_only_the_listed_accounts_are_protected(owners):
    first, second, third = owners
    assert is_protected(first) and is_protected(second) and not is_protected(third)


@override_settings(PROTECTED_SUPERUSER_EMAILS=["First.Owner@Example.test"])
def test_the_list_ignores_capitals(owners):
    from apps.accounts.protection import protected_emails

    assert FIRST in protected_emails() and is_protected(owners[0])


@pytest.mark.parametrize("change", [
    {"is_superuser": False}, {"is_staff": False}, {"is_active": False},
    {"staff_role": StaffRole.SUPPORT}, {"email": "someone.else@example.test"},
])
def test_a_protected_account_keeps_its_powers_and_email(owners, change):
    first = owners[0]
    for field, value in change.items():
        setattr(first, field, value)
    with pytest.raises(ProtectedAccount):
        first.save()
    first.refresh_from_db()
    assert first.is_superuser and first.is_active and first.staff_role == StaffRole.ADMIN and first.email == FIRST


def test_a_protected_account_can_never_be_deleted(owners):
    from django.db import transaction

    with pytest.raises(ProtectedAccount), transaction.atomic():
        owners[1].delete()
    with pytest.raises(ProtectedAccount), transaction.atomic():
        User.objects.filter(email__in=[FIRST, SECOND]).delete()
    assert User.objects.filter(email__in=[FIRST, SECOND]).count() == 2


def test_the_third_superuser_is_not_protected(owners):
    third = owners[2]
    third.is_active = False
    third.save()
    third.delete()
    assert not User.objects.filter(email=THIRD).exists()


def test_protected_accounts_can_still_look_after_themselves(owners):
    first = owners[0]
    first.full_name, first.phone = "New Name", "+2348031234567"
    first.set_password("Granite-Harbour-Willow-19")
    first.save()
    first.refresh_from_db()
    assert first.full_name == "New Name" and first.check_password("Granite-Harbour-Willow-19")


def test_django_admin_cannot_delete_or_demote_them(owners):
    first, _, third = owners
    c = login_client(third)
    from django.conf import settings

    base = f"/{settings.ADMIN_URL}accounts/user/"
    page = c.get(f"{base}{first.pk}/change/").content.decode()
    assert 'name="is_superuser"' not in page and 'name="is_active"' not in page and 'name="email"' not in page
    assert c.get(f"{base}{first.pk}/delete/").status_code == 403
    c.post(base, {"action": "delete_selected", "_selected_action": [str(first.pk), str(owners[1].pk)], "post": "yes"})
    assert User.objects.filter(email__in=[FIRST, SECOND]).count() == 2


def test_the_team_screen_cannot_touch_them(owners):
    with pytest.raises(PermissionFailure):
        team.remove_access(owners[2], owners[0].pk)


def test_create_staff_makes_superusers_with_a_phone(db, monkeypatch):
    from apps.accounts.management.commands import create_staff

    monkeypatch.setattr(create_staff.Command, "_password", lambda self, noinput: "Granite-Harbour-Willow-19")
    call_command("create_staff", FIRST, "--superuser", "--name", "First Owner", "--phone", "0803 123 4567",
                 stdout=open("/dev/null", "w"))  # noqa: SIM115
    user = User.objects.get(email=FIRST)
    assert user.is_superuser and user.is_staff and user.staff_role == StaffRole.ADMIN
    assert user.phone == "+2348031234567" and not user.phone_verified and not user.mfa_enabled
    with pytest.raises(CommandError, match="valid phone"):
        call_command("create_staff", "x@example.test", "--superuser", "--phone", "12")
    with pytest.raises(CommandError, match="protected"):
        call_command("create_staff", FIRST, "--role", "support", "--update")
