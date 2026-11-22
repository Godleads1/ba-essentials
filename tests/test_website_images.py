"""Website images: every upload is re-encoded, and the public only sees images a published page uses."""
from __future__ import annotations

import io
from html.parser import HTMLParser

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from apps.accounts.models import StaffRole
from apps.core.exceptions import ValidationFailure
from apps.files.storage import get_storage
from apps.website import images, markup, services
from apps.website.models import PageImage

from .conftest import login_client, make_user


@pytest.fixture(autouse=True)
def _private_root(settings, tmp_path):
    settings.PRIVATE_STORAGE_ROOT = str(tmp_path / "private")
    settings.PRIVATE_STORAGE_BACKEND = "local"


@pytest.fixture
def editor(db):
    return make_user("img.editor@example.test", staff_role=StaffRole.WEBSITE_EDITOR, mfa=True, is_staff=True)


@pytest.fixture
def publisher(db):
    return make_user("img.publisher@example.test", staff_role=StaffRole.WEBSITE_PUBLISHER, mfa=True, is_staff=True)


def _jpeg(size=(800, 600), exif_gps=False) -> bytes:
    image = Image.new("RGB", size, (51, 57, 127))
    out = io.BytesIO()
    if exif_gps:
        exif = Image.Exif()
        exif[0x8825] = {2: (6.0, 27.0, 0.0), 4: (3.0, 23.0, 0.0)}  # GPS: Lagos
        exif[0x010F] = "SyntheticCam"
        image.save(out, format="JPEG", exif=exif)
    else:
        image.save(out, format="JPEG")
    return out.getvalue()


def _upload(data: bytes, name="photo.jpg", content_type="image/jpeg"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def _stored(image: PageImage) -> bytes:
    with get_storage().open(image.storage_key) as handle:
        return handle.read()


# ---------- re-encoding ----------

def test_a_photo_is_resized_and_its_location_metadata_removed(editor):
    image = images.upload(uploaded_file=_upload(_jpeg((4000, 3000), exif_gps=True)), alt_text="Our office",
                          actor=editor)
    assert (image.width, image.height) == (2000, 1500) and image.content_type == "image/jpeg"
    assert image.storage_key.startswith("website/") and image.storage_key.endswith(".jpg")
    stored = Image.open(io.BytesIO(_stored(image)))
    assert stored.format == "JPEG" and dict(stored.getexif()) == {}
    assert b"SyntheticCam" not in _stored(image)


def test_transparency_is_kept_as_png(editor):
    out = io.BytesIO()
    Image.new("RGBA", (300, 200), (217, 102, 46, 128)).save(out, format="PNG")
    image = images.upload(uploaded_file=_upload(out.getvalue(), "logo.png", "image/png"), alt_text="Logo",
                          actor=editor)
    assert image.content_type == "image/png" and Image.open(io.BytesIO(_stored(image))).mode == "RGBA"


def test_hidden_payloads_do_not_survive(editor):
    polyglot = _jpeg() + b"<script>alert(1)</script><?php system($_GET['c']); ?>"
    image = images.upload(uploaded_file=_upload(polyglot), alt_text="Banner", actor=editor)
    assert b"<script>" not in _stored(image) and b"<?php" not in _stored(image)


def _gif() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (10, 10)).save(out, format="GIF")
    return out.getvalue()


def _bomb() -> bytes:
    out = io.BytesIO()
    Image.new("1", (10_000, 5_000)).save(out, format="PNG")  # 50 megapixels, a few KB on disk
    return out.getvalue()


@pytest.mark.parametrize("data,name", [
    (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', "logo.svg"),
    (b"<html><script>alert(1)</script></html>", "photo.jpg"),
    (b"%PDF-1.7 not an image", "photo.jpg"),
    (b"", "empty.jpg"),
    (_jpeg()[:200], "cut-off.jpg"),
])
def test_files_that_are_not_usable_images_are_refused(editor, data, name):
    with pytest.raises(ValidationFailure):
        images.upload(uploaded_file=_upload(data, name), alt_text="x", actor=editor)
    assert not PageImage.objects.exists()


def test_gifs_bombs_and_huge_files_are_refused(editor):
    for data in (_gif(), _bomb(), b"\xff\xd8" + b"0" * (images.MAX_UPLOAD_BYTES + 1)):
        with pytest.raises(ValidationFailure):
            images.upload(uploaded_file=_upload(data), alt_text="x", actor=editor)
    assert not PageImage.objects.exists()


@pytest.mark.parametrize("alt", ["", "   ", "x" * 201])
def test_a_description_is_required(editor, alt):
    with pytest.raises(ValidationFailure):
        images.upload(uploaded_file=_upload(_jpeg()), alt_text=alt, actor=editor)


# ---------- who can see it ----------

def _page_with(image, editor, publisher, *, publish=True, share=False):
    page, revision = services.create_page(slug="gallery", title="Gallery", summary="", actor=editor,
                                          body=f"Intro.\n\n{image.snippet}", share_image=image if share else None)
    if publish:
        services.publish(page, revision, actor=publisher)
    return page


def test_draft_images_are_hidden_until_their_page_is_published(editor, publisher):
    image = images.upload(uploaded_file=_upload(_jpeg()), alt_text="Team photo", actor=editor)
    url = f"/site-images/{image.pk}/"
    support = make_user("img.support@example.test", staff_role=StaffRole.SUPPORT, mfa=True, is_staff=True)
    assert Client().get(url).status_code == 404
    assert login_client(support).get(url).status_code == 404
    as_editor = login_client(editor).get(url)
    assert as_editor.status_code == 200 and as_editor["Cache-Control"] == "private, no-store"

    page = _page_with(image, editor, publisher, publish=False)
    assert Client().get(url).status_code == 404  # used only by a draft
    services.publish(page, page.revisions.first(), actor=publisher)
    public = Client().get(url)
    assert public.status_code == 200 and public["Content-Type"] == "image/jpeg"
    assert public["Cache-Control"] == "public, max-age=3600" and public["X-Content-Type-Options"] == "nosniff"
    assert b"".join(public.streaming_content).startswith(b"\xff\xd8")

    services.unpublish(page, actor=publisher)
    assert Client().get(url).status_code == 404


def test_published_page_shows_the_image_with_its_size_and_description(editor, publisher):
    image = images.upload(uploaded_file=_upload(_jpeg((640, 480))), alt_text="Our team", actor=editor)
    _page_with(image, editor, publisher)
    body = Client().get("/gallery/").content.decode()
    assert (f'<img src="/site-images/{image.pk}/" alt="Our team" width="640" height="480" loading="lazy"'
            in body)


def test_share_image_goes_in_the_link_preview(editor, publisher):
    image = images.upload(uploaded_file=_upload(_jpeg()), alt_text="Preview card", actor=editor)
    page, revision = services.create_page(slug="offer", title="Offer", summary="", body="Text.", actor=editor,
                                          share_image=image)
    services.publish(page, revision, actor=publisher)
    body = Client().get("/offer/").content.decode()
    from django.conf import settings as conf

    base = conf.APP_BASE_URL.rstrip("/")
    assert f'property="og:image" content="{base}/site-images/{image.pk}/"' in body
    assert Client().get(f"/site-images/{image.pk}/").status_code == 200  # the preview image is public too
    restored = services.restore_revision(page, revision, actor=editor)
    assert restored.share_image_id == image.pk


# ---------- markup ----------

class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_image_lines_are_safe_and_unknown_images_show_nothing(editor):
    image = images.upload(uploaded_file=_upload(_jpeg()), alt_text="Safe", actor=editor)
    lookup = {str(image.pk): image}
    html = markup.render(f'![" onerror="alert(1)](image:{image.pk})', lookup)
    parser = _Tags()
    parser.feed(html)
    assert [t for t, _ in parser.tags] == ["figure", "img"]
    attrs = parser.tags[1][1]
    assert set(attrs) == {"src", "alt", "width", "height", "loading", "decoding"}
    assert attrs["src"] == f"/site-images/{image.pk}/" and attrs["alt"] == '" onerror="alert(1)'
    assert markup.render("![x](image:00000000-0000-0000-0000-000000000000)", lookup) == ""
    assert "<img" not in markup.render("![x](https://evil.example/pixel.png)", lookup)
    assert "<img" not in markup.render(f"Inline ![x](image:{image.pk}) text", lookup)


# ---------- staff screens ----------

def test_editor_uploads_and_deletes_from_the_library(editor, publisher, django_capture_on_commit_callbacks):
    c = login_client(editor)
    response = c.post("/staff/website/images/", {"image": _upload(_jpeg()), "alt_text": "Desk"})
    assert response.status_code == 302
    image = PageImage.objects.get()
    assert image.snippet in c.get("/staff/website/images/").content.decode()

    page = _page_with(image, editor, publisher)
    c.post(f"/staff/website/images/{image.pk}/delete/")
    assert PageImage.objects.filter(pk=image.pk).exists()  # a published page uses it

    services.unpublish(page, actor=publisher)
    key = image.storage_key
    with django_capture_on_commit_callbacks(execute=True):
        c.post(f"/staff/website/images/{image.pk}/delete/")
    assert not PageImage.objects.filter(pk=image.pk).exists() and not get_storage().exists(key)


def test_bad_upload_shows_an_error_not_a_crash(editor):
    response = login_client(editor).post("/staff/website/images/", {
        "image": _upload(b"<svg/>", "x.svg", "image/svg+xml"), "alt_text": "Logo"})
    assert response.status_code == 200 and "JPEG, PNG or WebP" in response.content.decode()


def test_only_website_staff_can_upload(db):
    support = make_user("img.support2@example.test", staff_role=StaffRole.SUPPORT, mfa=True, is_staff=True)
    response = login_client(support).post("/staff/website/images/", {"image": _upload(_jpeg()), "alt_text": "x"})
    assert response.status_code == 302 and not PageImage.objects.exists()
    assert Client().post("/staff/website/images/", {"image": _upload(_jpeg()), "alt_text": "x"}).status_code == 302
    assert not PageImage.objects.exists()


def test_site_images_is_not_available_as_a_page_address(editor):
    with pytest.raises(ValidationFailure):
        services.create_page(slug="site-images", title="x", summary="", body="x", actor=editor)


def test_backups_include_website_images(editor, settings, tmp_path):
    from apps.core import backup_crypto, backups

    settings.BACKUP_DIR, settings.BACKUP_BACKEND = str(tmp_path / "backups"), "local"
    settings.BACKUP_ENCRYPTION_KEY = backup_crypto.generate_key()
    image = images.upload(uploaded_file=_upload(_jpeg()), alt_text="Backed up", actor=editor)
    target = backups.LocalTarget()
    backups.backup_files(target=target)
    assert f"files/{image.storage_key}" in {key for key, _ in target.list("files/")}
