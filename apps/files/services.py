from __future__ import annotations

import logging
import subprocess
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from apps.audit import services as audit_service

from .models import FilePurpose, PrivateFile, ScanStatus
from .storage import get_storage
from .validators import ValidatedUpload, validate_upload

logger = logging.getLogger("ba.files")


def _scan(storage_key: str) -> tuple[str, str]:
    backend = settings.FILE_SCAN_BACKEND
    if backend == "noop":
        return ScanStatus.CLEAN, "development scanner: files are not really scanned"
    if backend == "clamdscan":
        path = get_storage().path(storage_key)
        try:
            result = subprocess.run(
                ["clamdscan", "--no-summary", "--stdout", str(path)],
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


@transaction.atomic
def store_upload(
    *, organization, user, uploaded_file, purpose: str = FilePurpose.ATTACHMENT
) -> PrivateFile:
    validated: ValidatedUpload = validate_upload(uploaded_file)
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
