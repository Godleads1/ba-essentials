from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation


class PrivateStorage:
    """Local private storage. Never mounted under STATIC_ROOT or MEDIA_ROOT."""

    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.PRIVATE_STORAGE_ROOT)

    def _resolve(self, key: str) -> Path:
        if not key or ".." in key or key.startswith(("/", "\\")):
            raise SuspiciousFileOperation("Invalid storage key.")
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

    def delete(self, key: str) -> None:
        target = self._resolve(key)
        if target.is_file():
            target.unlink()


def get_storage() -> PrivateStorage:
    backend = settings.PRIVATE_STORAGE_BACKEND
    if backend == "local":
        return PrivateStorage()
    raise ImproperlyConfigured(
        f"PRIVATE_STORAGE_BACKEND={backend!r} is not implemented. "
        "Add an object-storage adapter before enabling it."
    )
