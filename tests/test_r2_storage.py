"""Private files in Cloudflare R2 (audit finding F4), tested against an in-memory stand-in for the bucket."""
from __future__ import annotations

import io

import pytest
from botocore.exceptions import ClientError
from django.core.checks import run_checks
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.test import override_settings

from apps.files import services as files
from apps.files import storage as storage_module
from apps.files.models import ScanStatus
from apps.files.storage import R2Storage, get_storage

from .conftest import login_client, pdf_upload


class FakeBucket:
    """The calls R2Storage makes, with S3's behaviour for a missing object."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[str] = []

    def upload_fileobj(self, fileobj, bucket, key):
        self.calls.append("upload")
        self.objects[key] = fileobj.read()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {}

    def download_fileobj(self, bucket, key, handle):
        handle.write(self.objects[key])

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


@pytest.fixture
def bucket(monkeypatch, settings):
    fake = FakeBucket()
    settings.PRIVATE_STORAGE_BACKEND = "r2"
    settings.R2_BUCKET = "banda-private"
    monkeypatch.setattr(storage_module, "R2Storage", lambda: R2Storage(client=fake, bucket="banda-private"))
    return fake


def test_uploads_go_to_the_bucket_not_the_disk(bucket, organization, owner, settings, tmp_path):
    settings.PRIVATE_STORAGE_ROOT = str(tmp_path)
    private = files.store_upload(organization=organization, user=owner, uploaded_file=pdf_upload())
    assert bucket.objects[private.storage_key].startswith(b"%PDF")
    assert private.storage_key.startswith(f"{organization.pk}/")
    assert not any(tmp_path.rglob("*.pdf"))


def test_authorised_download_streams_from_the_bucket(bucket, organization, owner, submitted_task):
    from apps.tasks.models import TaskAttachment

    private = files.store_upload(organization=organization, user=owner, uploaded_file=pdf_upload())
    type(private).objects.filter(pk=private.pk).update(scan_status=ScanStatus.CLEAN)
    TaskAttachment.objects.create(task=submitted_task, file=private)
    response = login_client(owner).get(f"/app/files/{private.pk}/download/")
    assert response.status_code == 200 and b"".join(response.streaming_content).startswith(b"%PDF")
    assert response["Content-Disposition"].startswith("attachment")


def test_other_businesses_still_cannot_download(bucket, organization, owner, submitted_task, other_owner):
    from apps.tasks.models import TaskAttachment

    private = files.store_upload(organization=organization, user=owner, uploaded_file=pdf_upload())
    TaskAttachment.objects.create(task=submitted_task, file=private)
    assert login_client(other_owner).get(f"/app/files/{private.pk}/download/").status_code == 403


def test_a_missing_object_is_reported_not_crashed(bucket, organization, owner, submitted_task):
    from apps.tasks.models import TaskAttachment

    private = files.store_upload(organization=organization, user=owner, uploaded_file=pdf_upload())
    type(private).objects.filter(pk=private.pk).update(scan_status=ScanStatus.CLEAN)
    TaskAttachment.objects.create(task=submitted_task, file=private)
    bucket.objects.clear()
    assert login_client(owner).get(f"/app/files/{private.pk}/download/").status_code == 404


def test_the_scanner_gets_a_temporary_copy_that_is_then_removed(bucket, organization, owner, settings, monkeypatch):
    settings.FILE_SCAN_BACKEND = "clamdscan"
    seen = {}

    class Result:
        returncode, stdout = 0, b""

    def fake_run(command, **kwargs):
        from pathlib import Path

        path = Path(command[-1])
        seen["path"], seen["body"] = path, path.read_bytes()
        return Result()

    monkeypatch.setattr(files.subprocess, "run", fake_run)
    private = files.store_upload(organization=organization, user=owner, uploaded_file=pdf_upload())
    assert private.scan_status == ScanStatus.CLEAN
    assert seen["body"].startswith(b"%PDF") and not seen["path"].exists()


def test_retention_deletes_from_the_bucket(bucket, organization, owner):
    private = files.store_upload(organization=organization, user=owner, uploaded_file=pdf_upload())
    get_storage().delete(private.storage_key)
    assert private.storage_key not in bucket.objects


@pytest.mark.parametrize("key", ["../etc/passwd", "/abs/key.pdf", "a\\b.pdf", ""])
def test_bad_keys_are_refused(key):
    with pytest.raises(SuspiciousFileOperation):
        R2Storage(client=FakeBucket(), bucket="b").open(key)


def test_unknown_backend_is_refused(settings):
    settings.PRIVATE_STORAGE_BACKEND = "s3-public"
    with pytest.raises(ImproperlyConfigured):
        get_storage()


@override_settings(PRIVATE_STORAGE_BACKEND="local", DEBUG=False)
def test_deploy_check_warns_about_local_disk():
    assert "banda.W003" in [m.id for m in run_checks(include_deployment_checks=True)]


@override_settings(PRIVATE_STORAGE_BACKEND="r2", DEBUG=False)
def test_deploy_check_quiet_with_r2():
    assert "banda.W003" not in [m.id for m in run_checks(include_deployment_checks=True)]


def test_calls_match_the_real_s3_client(settings):
    """botocore's Stubber rejects any call whose name or parameters the real S3 API wouldn't accept."""
    import boto3
    from botocore.stub import Stubber

    client = boto3.client("s3", endpoint_url="https://account.r2.cloudflarestorage.com", region_name="auto",
                          aws_access_key_id="synthetic", aws_secret_access_key="synthetic")
    store = R2Storage(client=client, bucket="banda-private")
    with Stubber(client) as stub:
        stub.add_response("head_object", {}, {"Bucket": "banda-private", "Key": "org/abc.pdf"})
        stub.add_client_error("head_object", "404", expected_params={"Bucket": "banda-private", "Key": "org/x.pdf"})
        stub.add_response("delete_object", {}, {"Bucket": "banda-private", "Key": "org/abc.pdf"})
        assert store.exists("org/abc.pdf") is True
        assert store.exists("org/x.pdf") is False
        store.delete("org/abc.pdf")
        stub.assert_no_pending_responses()
