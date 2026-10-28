from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.core.exceptions import ValidationFailure

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".docx",
    ".xlsx",
    ".csv",
    ".txt",
}

MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".docx": (b"PK\x03\x04",),
    ".xlsx": (b"PK\x03\x04",),
    ".doc": (b"\xd0\xcf\x11\xe0",),
}

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".doc": "application/msword",
    ".csv": "text/csv",
    ".txt": "text/plain",
}

CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class ValidatedUpload:
    extension: str
    content_type: str
    size_bytes: int
    sha256: str
    original_name: str


def sanitize_filename(name: str) -> str:
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = unicodedata.normalize("NFKC", base)
    base = CONTROL_CHARS.sub("", base).strip().strip(".")
    if not base:
        base = "file"
    if "." not in base:
        return base[:80]
    stem, _, ext = base.rpartition(".")
    stem = stem[:80] or "file"
    return f"{stem}.{ext.lower()}"


def validate_upload(uploaded_file) -> ValidatedUpload:
    if uploaded_file is None:
        raise ValidationFailure("Choose a file to upload.")
    if uploaded_file.size > settings.FILE_MAX_BYTES:
        limit_mb = settings.FILE_MAX_BYTES // (1024 * 1024)
        raise ValidationFailure(f"Files must be {limit_mb} MB or smaller.")

    safe_name = sanitize_filename(uploaded_file.name)
    extension = "." + safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationFailure(
            "That file type is not accepted. Upload a PDF, image, Word, Excel, "
            "CSV or text file."
        )

    uploaded_file.seek(0)
    header = uploaded_file.read(512)
    uploaded_file.seek(0)

    signatures = MAGIC_SIGNATURES.get(extension)
    if signatures and not any(header.startswith(sig) for sig in signatures):
        raise ValidationFailure(
            "The file contents do not match its extension. Upload the original file."
        )

    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)

    return ValidatedUpload(
        extension=extension,
        content_type=CONTENT_TYPES.get(extension, "application/octet-stream"),
        size_bytes=uploaded_file.size,
        sha256=digest.hexdigest(),
        original_name=safe_name,
    )


def validate_extension_list(extensions: set[str]) -> None:
    unknown = extensions - ALLOWED_EXTENSIONS
    if unknown:
        raise ValidationError(f"Unsupported extensions: {sorted(unknown)}")
