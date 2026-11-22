"""Delete data on the Privacy Policy's schedule (section 5). Run daily: `manage.py run_retention`.

| Record                                   | Kept                                   | Here                       |
| ---------------------------------------- | -------------------------------------- | -------------------------- |
| Account and business profile             | while active, and 90 days after closing | `delete_closed_profiles`   |
| Source and delivered documents for tasks | 6 years after the task closes          | `purge_task_documents`     |
| Support and complaint records            | 6 years                                | `purge_support`            |
| One-time sign-in codes                   | 1 day                                  | `purge_old_codes` (outbox) |
| Invoices, tax records, fee evidence      | 7 years                                | kept; not deleted yet      |
| Audit trail                              | as long as the longest record           | kept                       |
| IP addresses in the audit trail          | AUDIT_IP_RETENTION_DAYS (90)            | `clear_old_ip_addresses`   |

A business under a legal hold (set in admin) is skipped entirely. Every run that
deletes something writes an audit event with counts, never the deleted content.
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit import services as audit_service

PROFILE_DAYS = 90
TASK_DOCUMENT_YEARS = 6
SUPPORT_YEARS = 6


def _years(n: int) -> timedelta:
    return timedelta(days=365 * n + n // 4)


def run(now=None) -> dict:
    from apps.accounts.codes import purge_old_codes
    from apps.organizations.closure import close_due

    now = now or timezone.now()
    return {
        "accounts closed": close_due(now),
        "profiles deleted": delete_closed_profiles(now),
        "task documents deleted": purge_task_documents(now),
        "support threads deleted": purge_support(now),
        "old codes deleted": purge_old_codes(),
        "audit addresses cleared": audit_service.clear_old_ip_addresses(now),
    }


# ---------- account and business profile: 90 days after closing ----------

def _anonymise_user(user) -> None:
    from apps.accounts import services as account_services
    from apps.accounts.models import UserSession, VerificationChallenge
    from apps.communications.models import Notification

    keys = list(UserSession.objects.filter(user=user).values_list("session_key", flat=True))
    account_services._end_django_sessions(keys)
    UserSession.objects.filter(user=user).delete()
    VerificationChallenge.objects.filter(user=user).delete()
    Notification.objects.filter(user=user).delete()
    user.full_name = ""
    user.email = f"deleted-{user.pk.hex}@deleted.invalid"
    user.phone, user.phone_verified_at, user.email_verified_at = "", None, None
    user.is_active = False
    user.mfa_enabled, user.mfa_secret_encrypted, user.mfa_confirmed_at = False, "", None
    user.updates_by_email = user.marketing_by_email = False
    user.set_unusable_password()
    user.credential_version += 1
    user.save()


def delete_closed_profiles(now) -> int:
    from apps.organizations.models import Invitation, Membership, Organization

    cutoff = now - timedelta(days=PROFILE_DAYS)
    done = 0
    due = Organization.objects.filter(closed_at__lte=cutoff, profile_deleted_at__isnull=True, legal_hold=False)
    for pk in due.values_list("pk", flat=True):
        with transaction.atomic():
            org = Organization.objects.select_for_update().get(pk=pk)
            if org.profile_deleted_at is not None or org.legal_hold:
                continue
            people = 0
            for membership in Membership.objects.filter(organization=org).select_related("user"):
                user = membership.user
                if not user.is_client_account or not user.is_active:
                    continue
                # Someone who also belongs to a business that's still open keeps their account.
                elsewhere = Membership.objects.filter(user=user, organization__profile_deleted_at__isnull=True,
                                                      organization__closed_at__isnull=True).exclude(organization=org)
                if elsewhere.exists():
                    continue
                _anonymise_user(user)
                people += 1
            Invitation.objects.filter(organization=org).delete()
            org.name = "Closed business"
            org.slug = f"closed-{org.pk.hex[:12]}"
            org.profile_deleted_at = now
            org.save(update_fields=["name", "slug", "profile_deleted_at", "updated_at"])
            audit_service.record(action="retention.profile_deleted", organization=org, resource_type="organization",
                                 resource_id=str(org.pk), metadata={"people": people})
            done += 1
    return done


# ---------- task documents: 6 years after the task closes ----------

def _closed_tasks_before(cutoff):
    from django.db.models import Max, Q

    from apps.tasks.models import Task, TaskStatus

    return (
        Task.objects.filter(status__in=(TaskStatus.COMPLETED, TaskStatus.CANCELLED), organization__legal_hold=False)
        .annotate(closed=Max("status_events__created_at",
                             filter=Q(status_events__to_status__in=(TaskStatus.COMPLETED, TaskStatus.CANCELLED))))
        .filter(Q(completed_at__lte=cutoff) | Q(completed_at__isnull=True, closed__lte=cutoff)
                | Q(completed_at__isnull=True, closed__isnull=True, updated_at__lte=cutoff))
    )


def purge_task_documents(now) -> int:
    from apps.communications.models import TaskMessage
    from apps.files.models import PrivateFile
    from apps.files.storage import get_storage
    from apps.reviews.models import DeliverableVersion
    from apps.tasks.models import Task, TaskAttachment

    storage = get_storage()
    files = 0
    for task in _closed_tasks_before(now - _years(TASK_DOCUMENT_YEARS)).iterator():
        file_ids = set(TaskAttachment.objects.filter(task=task).values_list("file_id", flat=True))
        file_ids |= set(DeliverableVersion.objects.filter(deliverable__task=task).values_list("file_id", flat=True))
        pending = PrivateFile.objects.filter(pk__in=file_ids, purged_at__isnull=True)
        has_content = bool(task.brief or task.desired_outcome or task.client_information or task.needs_info_question)
        if not pending.exists() and not has_content and not TaskMessage.objects.filter(task=task).exists():
            continue
        with transaction.atomic():
            count = 0
            for f in pending.select_for_update():
                storage.delete(f.storage_key)
                f.purged_at = now
                f.save(update_fields=["purged_at", "updated_at"])
                count += 1
            TaskMessage.objects.filter(task=task).delete()
            Task.objects.filter(pk=task.pk).update(brief="", desired_outcome="", client_information="",
                                                   needs_info_question="")
            audit_service.record(action="retention.task_documents_deleted", organization_id=task.organization_id,
                                 resource_type="task", resource_id=task.reference, metadata={"files": count})
            files += count
    return files


# ---------- support records: 6 years ----------

def purge_support(now) -> int:
    from django.db.models import Q

    from apps.communications.models import SupportTicket, SupportTicketStatus

    cutoff = now - _years(SUPPORT_YEARS)
    due = SupportTicket.objects.filter(
        status__in=(SupportTicketStatus.RESOLVED, SupportTicketStatus.CLOSED), organization__legal_hold=False,
    ).filter(Q(resolved_at__lte=cutoff) | Q(resolved_at__isnull=True, last_message_at__lte=cutoff))
    done = 0
    for ticket in due.iterator():
        with transaction.atomic():
            reference, org_id = ticket.reference, ticket.organization_id
            ticket.delete()
            audit_service.record(action="retention.support_deleted", organization_id=org_id,
                                 resource_type="support_ticket", resource_id=reference)
            done += 1
    return done
