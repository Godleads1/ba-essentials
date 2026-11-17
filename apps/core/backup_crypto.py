"""Encrypting backups before they leave the server.

AES-256-GCM over 1 MiB chunks, so a large database dump never has to fit in memory.
Each chunk's nonce is a random 8-byte prefix (one per file) plus a 4-byte counter, and
its associated data is that counter plus a "last chunk" flag. So chunks can't be
reordered, dropped, swapped between files or cut off at the end without decryption
failing.

File layout: b"BANDABK1" | 8-byte nonce prefix | chunks, each as 4-byte length + ciphertext.
The key is BACKUP_ENCRYPTION_KEY: 32 random bytes, URL-safe base64. Keep it outside the
server too (a password manager): without it, no backup can be read.
"""
from __future__ import annotations

import base64
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

MAGIC = b"BANDABK1"
CHUNK = 1024 * 1024
MAX_CHUNKS = 2**32 - 1


class BackupDecryptError(Exception):
    pass


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _key(value: str | None = None) -> AESGCM:
    raw = value if value is not None else getattr(settings, "BACKUP_ENCRYPTION_KEY", "")
    try:
        key = base64.urlsafe_b64decode(raw.encode())
    except (ValueError, AttributeError) as exc:
        raise ImproperlyConfigured("BACKUP_ENCRYPTION_KEY is not valid base64.") from exc
    if len(key) != 32:
        raise ImproperlyConfigured("BACKUP_ENCRYPTION_KEY must be 32 bytes (make one with `backup_database --new-key`).")
    return AESGCM(key)


def _aad(counter: int, last: bool) -> bytes:
    return struct.pack(">IB", counter, 1 if last else 0)


def encrypt_stream(source, destination, key: str | None = None) -> None:
    aead = _key(key)
    prefix = os.urandom(8)
    destination.write(MAGIC + prefix)
    counter = 0
    chunk = source.read(CHUNK)
    while True:
        following = source.read(CHUNK)
        last = not following
        if counter >= MAX_CHUNKS:
            raise ValueError("Backup too large.")
        sealed = aead.encrypt(prefix + struct.pack(">I", counter), chunk, _aad(counter, last))
        destination.write(struct.pack(">I", len(sealed)) + sealed)
        if last:
            return
        counter += 1
        chunk = following


def decrypt_stream(source, destination, key: str | None = None) -> None:
    aead = _key(key)
    header = source.read(len(MAGIC) + 8)
    if len(header) != len(MAGIC) + 8 or not header.startswith(MAGIC):
        raise BackupDecryptError("Not a banda backup file.")
    prefix = header[len(MAGIC):]
    counter = 0
    pending = _read_sealed(source)
    if pending is None:
        raise BackupDecryptError("The backup is empty or cut off.")
    while pending is not None:
        following = _read_sealed(source)
        last = following is None
        try:
            plain = aead.decrypt(prefix + struct.pack(">I", counter), pending, _aad(counter, last))
        except Exception as exc:  # InvalidTag: wrong key, tampering, or a cut-off file
            raise BackupDecryptError("The backup can't be decrypted: wrong key, damaged or incomplete.") from exc
        destination.write(plain)
        counter += 1
        pending = following


def _read_sealed(source):
    size = source.read(4)
    if not size:
        return None
    if len(size) != 4:
        raise BackupDecryptError("The backup is cut off.")
    (length,) = struct.unpack(">I", size)
    if length > CHUNK + 16:
        raise BackupDecryptError("The backup is damaged.")
    sealed = source.read(length)
    if len(sealed) != length:
        raise BackupDecryptError("The backup is cut off.")
    return sealed
