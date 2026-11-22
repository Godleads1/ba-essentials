from __future__ import annotations

import hashlib
import logging
import subprocess
import uuid
from dataclasses import replace

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.db import transaction

from apps.audit import services as audit_service
from apps.core.exceptions import ValidationFailure

from .images import IMAGE_EXTENSIONS, strip_metadata
from .models import FilePurpose, PrivateFile, ScanStatus
from .storage import get_storage
from .validators import ValidatedUpload, validate_upload

logger = logging.getLogger("ba.files")


def _scan(storage_key: str) -> tuple[str, str]:
    backend = settings.FILE_SCAN_BACKEND
    if backend == "noop":
        return ScanStatus.CLEAN, "development scanner: files are not really scanned"
    if backend == "clamdscan":
        command = ["clamdscan", "--no-summary", "--stdout"]
        config_file = getattr(settings, "CLAMD_CONFIG_FILE", "")
        if config_file:
            # A TCP clamd may be on another host, so stream the file to it.
            command.extend(["--stream", f"--config-file={config_file}"])
        try:
            with get_storage().local_copy(storage_key) as path:
                result = subprocess.run(
                    [*command, str(path)],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("scan_backend_error %s", type(exc).__name__)
            return ScanStatus.FAILED, "scanner unavailable"
        output = (result.stdout or b"").decode("utf-8", "replace").strip()[:200]
        if result.returncode == 0:
            return ScanStatus.CLEAN, "clamdscan: no threat found"
        if result.returncode == 1:
            return ScanStatus.QUARANTINED, f"threat detected: {output}"
        return ScanStatus.FAILED, f"scanner error {result.returncode}"
    raise ImproperlyConfigured(f"Unknown FILE_SCAN_BACKEND {backend!r}")


def _check_storage_cap(organization, user, size: int) -> None:
    """A business's own uploads are capped (ORG_STORAGE_LIMIT_BYTES), so a signed-in account can't
    fill the storage. Files staff add (deliverables, receipts) never count against it or get refused."""
    limit = settings.ORG_STORAGE_LIMIT_BYTES
    if not limit or not getattr(user, "is_client_account", False):
        return
    from django.db.models import Sum

    from .models import PrivateFile

    used = PrivateFile.objects.filter(organization=organization, uploaded_by__isnull=False).exclude(
        uploaded_by__staff_role__gt="").aggregate(total=Sum("size_bytes"))["total"] or 0
    if used + size > limit:
        size_label = f"{limit / 1024 ** 3:g} GB" if limit >= 1024 ** 3 else f"{limit // 1024 ** 2} MB"
        raise ValidationFailure(
            f"Your business has used its {size_label} of file storage. Contact support to make more room."
        )


def _without_metadata(uploaded_file, validated: ValidatedUpload):
    """Photos lose their EXIF data (such as GPS position) before they are stored (apps/files/images.py)."""
    uploaded_file.seek(0)
    body = strip_metadata(uploaded_file.read(), validated.extension)
    return ContentFile(body), replace(validated, size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())


@transaction.atomic
def store_upload(
    *, organization, user, uploaded_file, purpose: str = FilePurpose.ATTACHMENT
) -> PrivateFile:
    validated: ValidatedUpload = validate_upload(uploaded_file)
    if validated.extension in IMAGE_EXTENSIONS:
        uploaded_file, validated = _without_metadata(uploaded_file, validated)
    _check_storage_cap(organization, user, validated.size_bytes)
    storage = get_storage()
    key = f"{organization.pk}/{uuid.uuid4().hex}{validated.extension}"
    storage.save(uploaded_file, key)

    private_file = PrivateFile.objects.create(
        organization=organization,
        uploaded_by=user if getattr(user, "is_authenticated", False) else None,
        purpose=purpose,
        storage_key=key,
        original_name=validated.original_name,
        size_bytes=validated.size_bytes,
        content_type=validated.content_type,
        sha256=validated.sha256,
    )
    status, detail = _scan(key)
    private_file.scan_status = status
    private_file.scan_detail = detail
    private_file.save(update_fields=["scan_status", "scan_detail", "updated_at"])

    audit_service.record(
        action="file.uploaded",
        actor=user,
        organization=organization,
        resource_type="file",
        resource_id=str(private_file.pk),
        metadata={"purpose": purpose, "scan": status},
    )
    return private_file


def rescan(private_file: PrivateFile) -> None:
    status, detail = _scan(private_file.storage_key)
    private_file.scan_status = status
    private_file.scan_detail = detail
    private_file.save(update_fields=["scan_status", "scan_detail", "updated_at"])
