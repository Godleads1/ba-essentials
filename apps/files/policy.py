from __future__ import annotations

from django.conf import settings
from django.core import signing
from django.urls import reverse

from .models import PrivateFile

DOWNLOAD_SALT = "ba.file-download"


def issue_download_url(private_file: PrivateFile, user) -> str:
    token = signing.dumps(
        {"f": str(private_file.pk), "u": str(user.pk)}, salt=DOWNLOAD_SALT
    )
    return (
        reverse("file-download", kwargs={"file_id": private_file.pk})
        + f"?token={token}"
    )


def token_matches(token: str, private_file: PrivateFile, user) -> bool:
    if not token:
        return False
    try:
        payload = signing.loads(
            token, salt=DOWNLOAD_SALT, max_age=settings.FILE_DOWNLOAD_TOKEN_TTL
        )
    except signing.BadSignature:
        return False
    return payload.get("f") == str(private_file.pk) and payload.get("u") == str(user.pk)


def can_view_file(user, private_file: PrivateFile) -> bool:
    """Every file decision is derived from the task it belongs to."""
    if not getattr(user, "is_authenticated", False):
        return False

    from apps.organizations.access import membership_for
    from apps.tasks.access import can_view_task
    from apps.tasks.models import TaskAttachment

    attachment = (
        TaskAttachment.objects.filter(file=private_file).select_related("task").first()
    )
    if attachment is not None:
        return can_view_task(user, attachment.task)

    from apps.reviews.models import DeliverableVersion, DeliverableVersionStatus

    version = (
        DeliverableVersion.objects.filter(file=private_file)
        .select_related("deliverable__task")
        .first()
    )
    if version is not None:
        task = version.deliverable.task
        if getattr(user, "staff_role", ""):
            return can_view_task(user, task)
        if membership_for(user, task.organization_id) is None:
            return False
        # Clients may only download deliverables after controlled release.
        return version.status == DeliverableVersionStatus.RELEASED

    return False
