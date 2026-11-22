from __future__ import annotations

import uuid
from contextvars import ContextVar

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
_client_ip: ContextVar[str] = ContextVar("client_ip", default="")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str:
    return _correlation_id.get() or "-"


def set_client_ip(value: str):
    """The visitor's address for the current request (apps/core/client_ip.py). Returns a reset token."""
    return _client_ip.set(value or "")


def reset_client_ip(token) -> None:
    _client_ip.reset(token)


def get_client_ip() -> str:
    return _client_ip.get()
