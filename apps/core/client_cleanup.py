"""Remove every client business and client account: a one-off clean-up of test data before launch.

Run by `manage.py remove_client_data`. Staff accounts (anyone with a staff role, Django staff or
superuser) are never touched, nor are plans, the service catalogue, website content or settings.

Safety:
- Pre-launch only: it refuses while a live Paystack or Flutterwave key is configured.
- It refuses while any business is under a legal hold.
- Without `commit=True` it runs inside a transaction that is rolled back, so the counts are exact
  and nothing changes.
- Stored files are deleted from storage only after the database change has committed.
- The audit trail is kept: its rows are unlinked from removed people and businesses (the
  append-only trigger allows exactly that), and one audit event records the counts.

Deletion order matters because invoices, payment records, acceptances and deliverables are
protected from accidental deletion (on_delete=PROTECT). Protected rows are removed first, but only
when they belong to the data being removed and to one of the models listed in REMOVABLE.
"""
from __future__ import annotations

import logging
from collections import Counter

from django.conf import settings
from django.db import transaction
from django.db.models.deletion import ProtectedError, RestrictedError
from django.db.models.signals import pre_delete

from apps.audit import services as audit_service

logger = logging.getLogger(__name__)

# Models whose rows may be removed to clear a PROTECT link while deleting a client business.
REMOVABLE = {
    "billing.invoice", "billing.paymentattempt", "billing.feerequest", "billing.feeline",
    "billing.renewalconsent", "billing.paymentmethod", "billing.usageledger",
    "quotes.quoteacceptance", "quotes.quoteversion", "reviews.deliverableversion",
    "reviews.clientacceptance", "reviews.review", "tasks.task", "tasks.assignment", "files.privatefile",
}


class Refused(Exception):
    """A safety check failed; nothing was changed."""


class _RolledBack(Exception):
    pass


def _live_keys() -> list[str]:
    live = []
    paystack = getattr(settings, "PAYSTACK_SECRET_KEY", "") or ""
    if paystack and not paystack.startswith("sk_test_"):
        live.append("Paystack")
    flutterwave = getattr(settings, "FLUTTERWAVE_SECRET_KEY", "") or ""
    if flutterwave and not flutterwave.startswith("FLWSECK_TEST-"):
        live.append("Flutterwave")
    return live


def client_users():
    from apps.accounts.models import User

    return User.objects.filter(staff_role="", is_staff=False, is_superuser=False)


def _delete(queryset, counts: Counter, depth: int = 0) -> None:
    """Delete, first removing any protected rows that are part of the same client data."""
    if depth > 12:
        raise Refused("The data is linked more deeply than expected; nothing was changed.")
    while True:
        try:
            _, by_model = queryset.delete()
        except (ProtectedError, RestrictedError) as error:
            blockers = list(getattr(error, "protected_objects", None) or error.restricted_objects)
            labels = {obj._meta.label_lower for obj in blockers}
            outside = labels - REMOVABLE
            if outside:
                raise Refused(f"Something outside client data depends on it ({', '.join(sorted(outside))}); "
                              "nothing was changed.") from error
            for label in labels:
                model = blockers[0]._meta.apps.get_model(label)
                ids = [obj.pk for obj in blockers if obj._meta.label_lower == label]
                _delete(model.objects.filter(pk__in=ids), counts, depth + 1)
            continue
        for label, n in by_model.items():
            counts[label] += n
        return


def run(*, commit: bool = False) -> dict:
    """Remove all client data. Returns {"counts": {...}, "files": n, "kept_staff": n, "committed": bool}."""
    from apps.accounts import services as account_services
    from apps.accounts.models import User, UserSession
    from apps.core.models import OutboxEvent
    from apps.files.models import PrivateFile
    from apps.organizations.models import Organization

    live = _live_keys()
    if live:
        raise Refused(f"Live payment keys are set ({' and '.join(live)}). This clean-up is for test data "
                      "before launch only; nothing was changed.")
    if Organization.objects.filter(legal_hold=True).exists():
        raise Refused("A business is under a legal hold; nothing was changed.")

    storage_keys: list[str] = []

    def remember_file(sender, instance, **kwargs):
        storage_keys.append(instance.storage_key)

    counts: Counter = Counter()
    result = {}
    pre_delete.connect(remember_file, sender=PrivateFile, weak=False, dispatch_uid="client_cleanup_files")
    try:
        with transaction.atomic():
            users = client_users()
            emails = {e.lower() for e in users.values_list("email", flat=True)}
            sessions = list(UserSession.objects.filter(user__in=users).values_list("session_key", flat=True))
            businesses = Organization.objects.count()

            _delete(Organization.objects.all(), counts)
            _delete(client_users(), counts)
            account_services._end_django_sessions(sessions)

            pending = 0
            for event in OutboxEvent.objects.filter(processed_at__isnull=True, topic=OutboxEvent.Topic.EMAIL):
                to = event.payload.get("to")
                recipients = [to] if isinstance(to, str) else list(to or [])
                if recipients and all(str(r).lower() in emails for r in recipients):
                    event.delete()
                    pending += 1
            counts["core.outboxevent (unsent emails to removed people)"] += pending

            kept_staff = User.objects.exclude(pk__in=client_users()).count()
            result = {"businesses": businesses, "people": len(emails), "counts": dict(sorted(counts.items())),
                      "files": len(storage_keys), "kept_staff": kept_staff, "committed": commit}
            audit_service.record(action="data.client_data_removed", resource_type="system",
                                 metadata={"businesses": businesses, "people": len(emails),
                                           "files": len(storage_keys)})
            if not commit:
                raise _RolledBack
            keys = list(storage_keys)
            transaction.on_commit(lambda: _delete_files(keys))
    except _RolledBack:
        pass
    finally:
        pre_delete.disconnect(sender=PrivateFile, dispatch_uid="client_cleanup_files")
    return result


def _delete_files(keys: list[str]) -> None:
    from apps.files.storage import get_storage

    storage = get_storage()
    for key in keys:
        try:
            storage.delete(key)
        except Exception:  # the database is already clean; a leftover file is only logged
            logger.warning("client clean-up: could not delete a stored file", exc_info=True)

