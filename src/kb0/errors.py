"""Typed errors mirroring kb0's structured error codes."""

from __future__ import annotations


class KbError(Exception):
    """Base class for all kb0 errors returned by the vault."""

    code = "ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class KbNotFoundError(KbError):
    code = "NOT_FOUND"


class KbConflictError(KbError):
    """Raised when vault.update is called with a stale expected_hash."""

    code = "CONFLICT"


class KbValidationError(KbError):
    code = "VALIDATION"


class KbACLDeniedError(KbError):
    code = "ACL_DENIED"


def error_from_text(text: str) -> KbError:
    """Map a kb0 error message back to a typed exception.

    kb0 formats each error code with a stable prefix, so matching on it is
    reliable (both sides live in the same project).
    """
    stripped = text.strip()
    if stripped.startswith("Not found"):
        return KbNotFoundError(text)
    if stripped.startswith("Conflict"):
        return KbConflictError(text)
    if stripped.startswith("Permission denied"):
        return KbACLDeniedError(text)
    if stripped.startswith("Validation"):
        return KbValidationError(text)
    return KbError(text)
