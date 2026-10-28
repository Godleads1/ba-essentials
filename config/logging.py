"""Logging configuration with redaction of secret-shaped values."""

from __future__ import annotations

import logging
import re

SENSITIVE_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|otp|code|session|cookie|mfa)",
    re.IGNORECASE,
)
REDACTED = "[redacted]"


class RedactSensitiveFilter(logging.Filter):
    """Redacts values for sensitive-looking keys in structured log extras.

    Only touches record attributes that are not part of the standard logging
    contract, so log formatting cannot break. NEVER add secrets to extra=...
    in the first place; this is defence in depth.
    """

    STANDARD_ATTRS = frozenset(
        vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
    ) | {"message", "asctime"}

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in list(vars(record).items()):
            if key in self.STANDARD_ATTRS:
                continue
            if SENSITIVE_KEY_RE.search(key) and value:
                setattr(record, key, REDACTED)
        return True


def build_logging(debug: bool = False) -> dict:
    level = "DEBUG" if debug else "INFO"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "redact": {"()": "config.logging.RedactSensitiveFilter"},
        },
        "formatters": {
            "structured": {
                "format": "%(asctime)s %(levelname)s %(name)s %(message)s "
                "request_id=%(request_id)s",
                "defaults": {"request_id": "-"},
            },
            "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "plain",
                "filters": ["redact"],
            },
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            "django.request": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
            "django.security": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
            "ba": {"handlers": ["console"], "level": level, "propagate": False},
        },
    }
