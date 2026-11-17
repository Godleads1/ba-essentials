"""Record (and so alert on) any change to who has staff or admin access, however it is made:
the create_staff command, the Django admin, or the shell."""
from __future__ import annotations

from django.conf import settings
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

FIELDS = ("staff_role", "is_staff", "is_superuser", "is_active")


def _access(user) -> dict:
    return {f: getattr(user, f, None) for f in FIELDS}


@receiver(pre_save, sender=settings.AUTH_USER_MODEL)
def remember_access(sender, instance, **kwargs):
    old = sender.objects.filter(pk=instance.pk).values(*FIELDS).first() if instance.pk else None
    instance._access_before = old


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def record_access_change(sender, instance, created, **kwargs):
    before = getattr(instance, "_access_before", None)
    after = _access(instance)
    if before is None:
        if not (after["staff_role"] or after["is_staff"] or after["is_superuser"]):
            return  # a new client account: not staff access
        before = {"staff_role": "", "is_staff": False, "is_superuser": False, "is_active": True}
    changed = {f: f"{before.get(f)!s} -> {after[f]!s}" for f in FIELDS if before.get(f) != after[f]}
    if not changed:
        return
    was_staff = bool(before.get("staff_role") or before.get("is_staff") or before.get("is_superuser"))
    if "is_active" in changed and len(changed) == 1 and not was_staff:
        return  # a client account switched on or off
    from . import services

    services.record(action="staff.access_changed", resource_type="user", resource_id=str(instance.pk),
                    metadata={"account": instance.email, **changed})
