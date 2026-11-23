"""Protected superusers: accounts nobody can delete, demote or switch off from the app.

Which accounts are protected is set on the server only, in PROTECTED_SUPERUSER_EMAILS
(deploy/.env), never in the code: the repository is public and the emails are personal data.

A protected account:
- can't be deleted (Django admin, the shell, or a cascade);
- keeps is_superuser, is_staff, the Admin role and is_active;
- keeps its email address (the protection follows the email);
- can still change its own password, name and phone, and reset its authenticator.

To remove a protected person (for example, they leave the business), someone with server
access takes their email out of PROTECTED_SUPERUSER_EMAILS and restarts the site. That needs
the server's SSH key, which is the point: nobody can do it from a browser.
"""
from __future__ import annotations

from django.conf import settings
from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver

from apps.core.exceptions import PermissionFailure

KEPT_FIELDS = ("is_superuser", "is_staff", "is_active", "staff_role", "email")


class ProtectedAccount(PermissionFailure):
    pass


def protected_emails() -> set[str]:
    return {e.strip().lower() for e in getattr(settings, "PROTECTED_SUPERUSER_EMAILS", []) if e.strip()}


def is_protected(user) -> bool:
    return bool(user is not None and (getattr(user, "email", "") or "").lower() in protected_emails())


@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def keep_protected_accounts(sender, instance, **kwargs):
    if not instance.pk:
        return
    before = sender.objects.filter(pk=instance.pk).values(*KEPT_FIELDS).first()
    if before is None or (before["email"] or "").lower() not in protected_emails():
        return
    from .models import StaffRole

    after = {f: getattr(instance, f) for f in KEPT_FIELDS}
    if (after["email"] or "").lower() != (before["email"] or "").lower():
        raise ProtectedAccount("This account is protected: its email address can't be changed.")
    if not (after["is_superuser"] and after["is_staff"] and after["is_active"]) \
            or after["staff_role"] != StaffRole.ADMIN:
        raise ProtectedAccount("This account is protected: it stays an active superuser.")


@receiver(pre_delete, sender=settings.AUTH_USER_MODEL)
def never_delete_protected_accounts(sender, instance, **kwargs):
    if is_protected(instance):
        raise ProtectedAccount("This account is protected and can't be deleted.")
