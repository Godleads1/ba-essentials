"""Images for website pages.

Every upload is opened with Pillow, checked and saved again as a fresh JPEG or
PNG. That drops anything hidden in the file (metadata such as GPS location,
scripts in SVG, polyglot payloads), so only plain pixels are ever served.
Images are stored privately and shown to the public only once a published page
uses them (apps/website/views_public.site_image).
"""
from __future__ import annotations

import io
import secrets
import uuid
import warnings

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.audit import services as audit
from apps.core.exceptions import ValidationFailure
from apps.files.storage import get_storage

from .models import Page, PageImage, PageRevision

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_SOURCE_PIXELS = 40_000_000  # refuses "decompression bombs" before they are decoded
MAX_EDGE = 2000  # longest side after resizing; plenty for a page or a link preview
MAX_ALT_TEXT = 200
ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP"}
KEY_PREFIX = "website/"


def _decode(data: bytes) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            if probe.format not in ACCEPTED_FORMATS:
                raise ValidationFailure("Upload a JPEG, PNG or WebP image.")
            if probe.width * probe.height > MAX_SOURCE_PIXELS:
                raise ValidationFailure("That image is too large. Use one under 40 megapixels.")
            probe.verify()  # structural check; the image must be opened again afterwards
            image = Image.open(io.BytesIO(data))
            image.load()
    except ValidationFailure:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValidationFailure("That file isn't an image we can use. Upload a JPEG, PNG or WebP.") from exc
    return image


def _reencode(image: Image.Image) -> tuple[bytes, str, str, int, int]:
    image = ImageOps.exif_transpose(image)  # keep the orientation the camera recorded, then drop the metadata
    image.thumbnail((MAX_EDGE, MAX_EDGE))
    has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
    out = io.BytesIO()
    if has_alpha:
        image = image.convert("RGBA")
        image.save(out, format="PNG", optimize=True)
        content_type, ext = "image/png", "png"
    else:
        image = image.convert("RGB")
        image.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
        content_type, ext = "image/jpeg", "jpg"
    return out.getvalue(), content_type, ext, image.width, image.height


@transaction.atomic
def upload(*, uploaded_file, alt_text: str, actor) -> PageImage:
    alt_text = " ".join((alt_text or "").split())
    if not alt_text:
        raise ValidationFailure("Describe the image for people who can't see it (alt text).")
    if len(alt_text) > MAX_ALT_TEXT:
        raise ValidationFailure(f"Keep the description to {MAX_ALT_TEXT} characters or fewer.")
    if uploaded_file is None or uploaded_file.size > MAX_UPLOAD_BYTES:
        raise ValidationFailure("Upload an image of 5 MB or less.")
    data = uploaded_file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationFailure("Upload an image of 5 MB or less.")
    body, content_type, ext, width, height = _reencode(_decode(data))
    key = f"{KEY_PREFIX}{secrets.token_hex(16)}.{ext}"
    get_storage().save(ContentFile(body), key)
    image = PageImage.objects.create(
        storage_key=key, content_type=content_type, alt_text=alt_text, width=width, height=height,
        size_bytes=len(body), uploaded_by=actor,
    )
    audit.record(action="website.image_uploaded", actor=actor, resource_type="website_image",
                 resource_id=str(image.pk), reason=f"{width}x{height}")
    return image


def _published_revisions():
    return PageRevision.objects.filter(pk__in=Page.objects.exclude(published_revision=None)
                                       .values("published_revision"))


def is_live(image: PageImage) -> bool:
    """True when a published page shows the image, in its text or as its link-preview image."""
    return _published_revisions().filter(Q(body__contains=f"image:{image.pk}") | Q(share_image=image)).exists()


@transaction.atomic
def delete(image: PageImage, *, actor) -> None:
    if is_live(image):
        raise ValidationFailure("A published page uses this image. Remove it from the page and publish first.")
    key = image.storage_key
    audit.record(action="website.image_deleted", actor=actor, resource_type="website_image",
                 resource_id=str(image.pk), reason=image.alt_text[:80])
    image.delete()
    transaction.on_commit(lambda: get_storage().delete(key))


def dimensions(ids) -> dict:
    """{id string: image} for the images a page body refers to (unknown ids are left out)."""
    clean = []
    for value in ids:
        try:
            clean.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return {str(img.pk): img for img in PageImage.objects.filter(pk__in=clean)}
