"""kb0 — Python client for the kb0 knowledge base layer for AI agents."""

from .client import VaultClient
from .errors import (
    KbACLDeniedError,
    KbConflictError,
    KbError,
    KbNotFoundError,
    KbValidationError,
)

__version__ = "0.3.0"

__all__ = [
    "VaultClient",
    "KbError",
    "KbNotFoundError",
    "KbConflictError",
    "KbValidationError",
    "KbACLDeniedError",
]
