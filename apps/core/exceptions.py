from __future__ import annotations


class DomainError(Exception):
    """A business rule was violated. Safe to show to the user."""

    code = "domain_error"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class ValidationFailure(DomainError):
    code = "validation_failed"
    http_status = 400


class PermissionFailure(DomainError):
    code = "permission_denied"
    http_status = 403


class ObjectNotFound(DomainError):
    code = "not_found"
    http_status = 404


class InvalidTransition(DomainError):
    code = "invalid_transition"
    http_status = 409


class StaleState(DomainError):
    code = "stale_state"
    http_status = 409


class IdempotencyConflict(DomainError):
    code = "idempotency_conflict"
    http_status = 409


class PaymentRequired(DomainError):
    code = "payment_required"
    http_status = 402


class RateLimited(DomainError):
    code = "rate_limited"
    http_status = 429
