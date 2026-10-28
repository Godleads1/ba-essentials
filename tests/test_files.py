from __future__ import annotations

import subprocess

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.core.exceptions import ValidationFailure
from apps.files import services
from apps.files.models import PrivateFile, ScanStatus
from apps.files.policy import issue_download_url, token_matches
from apps.files.validators import sanitize_filename, validate_upload
from apps.tasks.models import TaskAttachment

from .conftest import pdf_upload


def test_disallowed_extension_is_rejected():
    with pytest.raises(ValidationFailure):
        validate_upload(pdf_upload("payload.exe", b"MZ\x90\x00"))


def test_extension_must_match_file_contents():
    with pytest.raises(ValidationFailure):
        validate_upload(pdf_upload("fake.pdf", b"this is plain text"))


def test_filename_is_stripped_of_paths_and_control_characters():
    assert sanitize_filename("..\\..\\Invoice\x00 Final.pdf") == "Invoice Final.pdf"
    assert sanitize_filename("../../etc/passwd") == "passwd"


@override_settings(FILE_MAX_BYTES=16)
def test_oversized_upload_is_rejected():
    with pytest.raises(ValidationFailure):
        validate_upload(pdf_upload("big.pdf", b"%PDF-" + b"x" * 64))


def test_store_upload_uses_opaque_key_and_records_hash(organization, owner):
    uploaded = pdf_upload("client brief.pdf")

    private = services.store_upload(
        organization=organization, user=owner, uploaded_file=uploaded
    )

    assert private.storage_key.startswith(f"{organization.pk}/")
    assert "client brief" not in private.storage_key
    assert private.original_name == "client brief.pdf"
    assert private.scan_status == ScanStatus.CLEAN
    assert len(private.sha256) == 64
    assert services.get_storage().exists(private.storage_key)


def test_remote_clamd_streams_with_the_configured_tcp_config(
    organization, owner, monkeypatch
):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(services.subprocess, "run", fake_run)
    with override_settings(
        FILE_SCAN_BACKEND="clamdscan",
        CLAMD_CONFIG_FILE="/tmp/clamd.conf",
    ):
        private = services.store_upload(
            organization=organization, user=owner, uploaded_file=pdf_upload()
        )

    assert private.scan_status == ScanStatus.CLEAN
    assert calls[0][:3] == ["clamdscan", "--no-summary", "--stdout"]
    assert "--stream" in calls[0]
    assert "--config-file=/tmp/clamd.conf" in calls[0]


def test_download_token_is_bound_to_user_and_expires(organization, owner, other_owner):
    private = services.store_upload(
        organization=organization, user=owner, uploaded_file=pdf_upload()
    )
    url = issue_download_url(private, owner)
    token = url.split("token=", 1)[1]

    assert token_matches(token, private, owner) is True
    assert token_matches(token, private, other_owner) is False
    assert token_matches("garbage", private, owner) is False

    with override_settings(FILE_DOWNLOAD_TOKEN_TTL=-1):
        assert token_matches(token, private, owner) is False


def test_unattached_file_is_not_downloadable(client, organization, owner):
    private = services.store_upload(
        organization=organization, user=owner, uploaded_file=pdf_upload()
    )

    response = client.get(reverse("file-download", args=[private.pk]))

    assert response.status_code == 403


def test_quarantined_file_is_blocked_with_409(client, organization, owner, submitted_task):
    private = services.store_upload(
        organization=organization, user=owner, uploaded_file=pdf_upload()
    )
    TaskAttachment.objects.create(task=submitted_task, file=private, added_by=owner)
    PrivateFile.objects.filter(pk=private.pk).update(scan_status=ScanStatus.QUARANTINED)

    response = client.get(reverse("file-download", args=[private.pk]))

    assert response.status_code == 409


def test_clean_attachment_downloads_for_owning_tenant(client, organization, owner, submitted_task):
    payload = b"%PDF-1.4\n% synthetic invoice\n"
    private = services.store_upload(
        organization=organization,
        user=owner,
        uploaded_file=pdf_upload("invoice.pdf", payload),
    )
    TaskAttachment.objects.create(task=submitted_task, file=private, added_by=owner)

    response = client.get(reverse("file-download", args=[private.pk]))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == payload
    assert response["Cache-Control"] == "private, no-store"
