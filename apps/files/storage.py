from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation


def _check_key(key: str) -> str:
    """Storage keys are made by the server (`<org id>/<random hex><.ext>`); anything else is refused."""
    if not key or ".." in key or key.startswith(("/", "\\")) or "\\" in key:
        raise SuspiciousFileOperation("Invalid storage key.")
    return key


class PrivateStorage:
    """Local private storage. Never mounted under STATIC_ROOT or MEDIA_ROOT. For development and tests."""

    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.PRIVATE_STORAGE_ROOT)

    def _resolve(self, key: str) -> Path:
        _check_key(key)
        root = self.root.resolve()
        target = (root / key).resolve()
        if root not in target.parents and target != root:
            raise SuspiciousFileOperation("Storage key escapes the private root.")
        return target

    def save(self, file_obj, key: str) -> None:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as destination:
            for chunk in file_obj.chunks():
                destination.write(chunk)

    def open(self, key: str):
        return self._resolve(key).open("rb")

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def path(self, key: str) -> Path:
        return self._resolve(key)

    @contextmanager
    def local_copy(self, key: str):
        """A path on this machine holding the file, for the malware scanner."""
        yield self._resolve(key)

    def delete(self, key: str) -> None:
        target = self._resolve(key)
        if target.is_file():
            target.unlink()


class R2Storage:
    """Private files in a Cloudflare R2 bucket (S3-compatible). The bucket must not be public.

    Objects are never given public URLs: every download goes through the app's
    authorised download view, which streams the object to the signed-in user.
    R2 encrypts objects at rest.
    """

    def __init__(self, client=None, bucket: str | None = None):
        self.bucket = bucket or settings.R2_BUCKET
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=settings.R2_ENDPOINT_URL,
                aws_access_key_id=settings.R2_ACCESS_KEY_ID,
                aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
                region_name="auto",
                config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"},
                              connect_timeout=10, read_timeout=60),
            )
        return self._client

    def save(self, file_obj, key: str) -> None:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        self.client.upload_fileobj(file_obj, self.bucket, _check_key(key))

    def open(self, key: str):
        return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"]

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=_check_key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    @contextmanager
    def local_copy(self, key: str):
        """Download to a private temporary file for the scanner; it is deleted afterwards."""
        with tempfile.NamedTemporaryFile(prefix="banda-scan-", suffix=Path(key).suffix) as handle:
            self.client.download_fileobj(self.bucket, _check_key(key), handle)
            handle.flush()
            yield Path(handle.name)

    def path(self, key: str) -> Path:
        raise NotImplementedError("Files in R2 have no local path; use local_copy().")

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_check_key(key))


def get_storage():
    backend = settings.PRIVATE_STORAGE_BACKEND
    if backend == "local":
        return PrivateStorage()
    if backend == "r2":
        return R2Storage()
    raise ImproperlyConfigured(f"PRIVATE_STORAGE_BACKEND={backend!r} is not supported. Use 'local' or 'r2'.")
