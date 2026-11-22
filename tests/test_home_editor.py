"""Editable home page text: drafts, preview, publishing, permissions and safety."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import Client

from apps.accounts.models import StaffRole
from apps.audit.models import AuditEvent
from apps.core.exceptions import PermissionFailure, ValidationFailure
from apps.website import home
from apps.website.models import HomeRevision

from .conftest import login_client, make_user

TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "banda" / "public"


@pytest.fixture
def editor(db):
    return make_user("home.editor@example.test", staff_role=StaffRole.WEBSITE_EDITOR, mfa=True, is_staff=True)


@pytest.fixture
def publisher(db):
    return make_user("home.publisher@example.test", staff_role=StaffRole.WEBSITE_PUBLISHER, mfa=True, is_staff=True)


def _visitor_home() -> str:
    return Client().get("/").content.decode()


def _content(**changes) -> dict:
    data = home.defaults()
    data.update(changes)
    return data


def _post_data(content: dict, faqs: list[dict] | None = None) -> dict:
    data = {k: v for k, v in content.items() if k in home.FIELDS}
    faqs = content["faqs"] if faqs is None else faqs
    data.update({"faq-TOTAL_FORMS": str(len(faqs)), "faq-INITIAL_FORMS": "0", "faq-MIN_NUM_FORMS": "0",
                 "faq-MAX_NUM_FORMS": "20"})
    for i, faq in enumerate(faqs):
        data.update({f"faq-{i}-question": faq.get("question", ""), f"faq-{i}-answer": faq.get("answer", ""),
                     f"faq-{i}-ORDER": str(faq.get("ORDER", i + 1))})
        if faq.get("DELETE"):
            data[f"faq-{i}-DELETE"] = "on"
    return data


# ---------- the template uses every field ----------

def test_every_editable_field_appears_on_the_page():
    body = (TEMPLATES / "_landing_body.html").read_text() + (TEMPLATES / "landing.html").read_text()
    used = set(re.findall(r"home\.(\w+)", body))
    assert set(home.FIELDS) <= used, set(home.FIELDS) - used


def test_until_something_is_published_the_page_reads_as_it_always_did(db):
    body = _visitor_home()
    assert "Get More Done with" in body and "Still have questions?" in body
    assert body.count("<details") == len(home.DEFAULT_FAQS)
    assert '<a href="/services/">browse every task</a>' in body  # links in answers still work
    assert "<title>banda — B &amp; A Essentials | Beyond your AI</title>" in body


# ---------- drafts, publishing, going back ----------

def test_a_draft_changes_nothing_until_it_is_published(editor, publisher):
    revision = home.save_draft(_content(hero_title="Your back office, sorted", seo_title="Business support in Lagos"),
                               actor=editor)
    assert "Your back office, sorted" not in _visitor_home()
    home.publish(revision, actor=publisher)
    body = _visitor_home()
    assert "Your back office, sorted" in body and "<title>Business support in Lagos</title>" in body
    assert AuditEvent.objects.filter(action="website.home_published").exists()

    home.use_original(actor=publisher)
    assert "Get More Done with" in _visitor_home() and HomeRevision.objects.count() == 1  # versions are kept


def test_restoring_makes_a_new_draft_with_the_old_words(editor, publisher):
    first = home.save_draft(_content(hero_title="First wording"), actor=editor)
    home.save_draft(_content(hero_title="Second wording"), actor=editor)
    copy = home.restore(first, actor=editor)
    assert copy.number == 3 and copy.content["hero_title"] == "First wording"


def test_only_publishers_publish(editor, publisher):
    revision = home.save_draft(_content(hero_title="Not yet"), actor=editor)
    with pytest.raises(PermissionFailure):
        home.publish(revision, actor=editor)
    with pytest.raises(PermissionFailure):
        home.use_original(actor=editor)
    response = login_client(editor).post(f"/staff/website/home/{revision.pk}/publish/")
    assert response.status_code == 302 and "Not yet" not in _visitor_home()
    login_client(publisher).post(f"/staff/website/home/{revision.pk}/publish/")
    assert "Not yet" in _visitor_home()


def test_other_staff_and_visitors_cannot_reach_the_editor(db, editor):
    revision = home.save_draft(_content(), actor=editor)
    support = make_user("home.support@example.test", staff_role=StaffRole.SUPPORT, mfa=True, is_staff=True)
    for client in (Client(), login_client(support)):
        for url in ("/staff/website/home/", "/staff/website/home/versions/",
                    f"/staff/website/home/{revision.pk}/preview/"):
            assert client.get(url).status_code == 302
        client.post(f"/staff/website/home/{revision.pk}/restore/")
    assert HomeRevision.objects.count() == 1


# ---------- the editor screen ----------

def test_editor_saves_reorders_removes_and_adds_questions(editor):
    faqs = [{"question": "First?", "answer": "One.", "ORDER": 2},
            {"question": "Second?", "answer": "Two.", "ORDER": 1},
            {"question": "Gone?", "answer": "Removed.", "ORDER": 3, "DELETE": True},
            {"question": "", "answer": "", "ORDER": ""},  # an empty extra row is ignored
            {"question": "New?", "answer": "**Added**, see [terms](/terms/).", "ORDER": 4}]
    c = login_client(editor)
    assert c.get("/staff/website/home/").status_code == 200
    response = c.post("/staff/website/home/", _post_data(_content(hero_title="Edited"), faqs))
    assert response.status_code == 302 and response["Location"] == "/staff/website/home/versions/"
    saved = HomeRevision.objects.get().content
    assert saved["hero_title"] == "Edited"
    assert [f["question"] for f in saved["faqs"]] == ["Second?", "First?", "New?"]


def test_the_editor_starts_from_the_newest_version(editor):
    home.save_draft(_content(hero_title="Newest words"), actor=editor)
    assert "Newest words" in login_client(editor).get("/staff/website/home/").content.decode()


def test_empty_or_too_long_text_is_refused_with_a_message(editor):
    c = login_client(editor)
    response = c.post("/staff/website/home/", _post_data(_content(hero_title="")))
    assert response.status_code == 200 and not HomeRevision.objects.exists()
    response = c.post("/staff/website/home/", _post_data(_content(seo_description="x" * 161)))
    assert response.status_code == 200 and not HomeRevision.objects.exists()


@pytest.mark.parametrize("faqs", [
    [{"question": "No answer?", "answer": ""}],
    [{"question": "", "answer": "No question."}],
    [{"question": f"Q{i}?", "answer": "A."} for i in range(home.MAX_FAQS + 1)],
    [{"question": "x" * 161, "answer": "A."}],
])
def test_bad_questions_are_refused(editor, faqs):
    with pytest.raises(ValidationFailure):
        home.save_draft(_content(faqs=faqs), actor=editor)


def test_preview_shows_the_draft_and_is_never_indexed(editor):
    revision = home.save_draft(_content(hero_title="Draft headline"), actor=editor)
    body = login_client(editor).get(f"/staff/website/home/{revision.pk}/preview/").content.decode()
    assert "Draft headline" in body and "Preview of home page v1" in body
    assert '<meta name="robots" content="noindex" />' in body and 'rel="canonical"' not in body


# ---------- safety ----------

def test_typed_html_never_becomes_live_markup(editor, publisher):
    attack = '<script>alert(1)</script><img src=x onerror=alert(1)>'
    revision = home.save_draft(_content(
        hero_title=attack, hero_intro=attack + " [x](javascript:alert(1))", seo_title='"><script>alert(2)</script>',
        faqs=[{"question": attack, "answer": attack + " [ok](/services/)"}]), actor=editor)
    home.publish(revision, actor=publisher)
    body = _visitor_home()
    assert "<script>alert" not in body and "<img src=x" not in body and 'href="javascript' not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert '<a href="/services/">ok</a>' in body


def test_headings_are_plain_text_and_paragraphs_get_formatting(db):
    copy = home.Copy({"hero_title": "**not bold**", "hero_intro": "**bold** and [link](/services/)\nnext line"})
    assert copy.hero_title == "**not bold**"
    assert copy.hero_intro == '<strong>bold</strong> and <a href="/services/">link</a><br>next line'


def test_a_version_saved_under_older_rules_is_checked_again_before_publishing(editor, publisher):
    revision = HomeRevision.objects.create(number=1, content={**home.defaults(), "hero_title": ""}, created_by=editor)
    with pytest.raises(ValidationFailure):
        home.publish(revision, actor=publisher)


def test_unknown_keys_are_dropped(editor):
    revision = home.save_draft({**_content(), "evil": "<script>", "pricing_data": "x"}, actor=editor)
    assert "evil" not in revision.content and "pricing_data" not in revision.content


def test_link_preview_image(editor, publisher, settings, tmp_path):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from apps.website import images

    settings.PRIVATE_STORAGE_ROOT, settings.PRIVATE_STORAGE_BACKEND = str(tmp_path), "local"
    out = io.BytesIO()
    Image.new("RGB", (1200, 630), (51, 57, 127)).save(out, "JPEG")
    image = images.upload(uploaded_file=SimpleUploadedFile("og.jpg", out.getvalue(), "image/jpeg"),
                          alt_text="banda", actor=editor)
    home.publish(home.save_draft(_content(share_image=str(image.pk)), actor=editor), actor=publisher)
    body = _visitor_home()
    assert f'property="og:image" content="http://testserver/site-images/{image.pk}/"' in body
    assert Client().get(f"/site-images/{image.pk}/").status_code == 200  # the home page's image is public


def test_the_home_page_image_cannot_be_deleted_while_live(editor, publisher, settings, tmp_path):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from apps.website import images

    settings.PRIVATE_STORAGE_ROOT, settings.PRIVATE_STORAGE_BACKEND = str(tmp_path), "local"
    out = io.BytesIO()
    Image.new("RGB", (100, 100)).save(out, "JPEG")
    image = images.upload(uploaded_file=SimpleUploadedFile("og.jpg", out.getvalue(), "image/jpeg"), alt_text="x",
                          actor=editor)
    home.publish(home.save_draft(_content(share_image=str(image.pk)), actor=editor), actor=publisher)
    with pytest.raises(ValidationFailure):
        images.delete(image, actor=editor)
    home.use_original(actor=publisher)
    assert not images.is_live(image)
