"""Remove hidden metadata from uploaded photos (security checklist 18).

Phone photos carry EXIF data, often including the GPS position where the photo was taken,
and an image file can hide other data in comments or extra chunks. Every PNG or JPEG
uploaded with a request or a deliverable is opened with Pillow and saved again with only
its pixels: same format, same size. A JPEG keeps its own quality tables, so the picture is
not visibly changed; a photo the camera stored sideways is turned the right way up first. The original bytes are never stored.
"""
from __future__ import annotations

import io
import warnings

from PIL import Image, ImageOps, JpegImagePlugin, UnidentifiedImageError

from apps.core.exceptions import ValidationFailure

IMAGE_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}
MAX_PIXELS = 80_000_000  # refuses "decompression bombs" before they are decoded
UNREADABLE = "That image couldn't be read. Export it again as a PNG or JPEG and upload that."


def strip_metadata(data: bytes, extension: str) -> bytes:
    """Return the image saved again without metadata. Raises ValidationFailure if it isn't one."""
    expected = IMAGE_EXTENSIONS[extension]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(io.BytesIO(data))
            if probe.format != expected or probe.width * probe.height > MAX_PIXELS:
                raise ValidationFailure(UNREADABLE)
            probe.verify()
            image = Image.open(io.BytesIO(data))
            image.load()
    except ValidationFailure:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValidationFailure(UNREADABLE) from exc

    out = io.BytesIO()
    if expected == "JPEG":
        tables, sampling = image.quantization, JpegImagePlugin.get_sampling(image)
        if image.getexif().get(0x0112, 1) in (None, 1):
            pixels = image.copy()
        else:
            pixels = ImageOps.exif_transpose(image)  # the camera's rotation becomes real pixels
        pixels.info = {}  # no EXIF, comments or other extras are carried over
        # The file's own quantisation tables, so the picture is not visibly changed.
        options = {"qtables": tables, "subsampling": sampling} if tables and sampling >= 0 else {"quality": 95}
        pixels.save(out, format="JPEG", optimize=True, **options)
    else:
        # A fresh PNG carries no text, EXIF or other chunks; only transparency is kept.
        transparency = image.info.get("transparency")
        pixels = image.copy()
        pixels.info = {}
        params = {"transparency": transparency} if transparency is not None else {}
        pixels.save(out, format="PNG", optimize=True, **params)
    return out.getvalue()
