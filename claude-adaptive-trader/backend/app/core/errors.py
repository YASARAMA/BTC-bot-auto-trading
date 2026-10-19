"""Exceptions with a stable machine-readable code, mapped to HTTP responses by the API."""
from __future__ import annotations

from typing import Any


class TraderError(Exception):
    code = "error"
    status_code = 400

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, "details": self.details}


class NotFound(TraderError):
    code = "not_found"
    status_code = 404


class Conflict(TraderError):
    code = "conflict"
    status_code = 409


class InvalidRequest(TraderError):
    code = "invalid_request"
    status_code = 422


class DataUnavailable(TraderError):
    """The data needed for a decision does not exist locally and cannot be fetched."""

    code = "data_unavailable"
    status_code = 503


class DataValidationError(TraderError):
    code = "data_invalid"
    status_code = 422


class ExchangeUnavailable(TraderError):
    code = "exchange_unavailable"
    status_code = 503


class TestSetLocked(TraderError):
    """Raised whenever something tries to read the frozen test segment without a logged final evaluation."""

    code = "test_set_locked"
    status_code = 403


class SafetyViolation(TraderError):
    """A request would break a hard safety rule. These are never negotiable at runtime."""

    code = "safety_violation"
    status_code = 403
