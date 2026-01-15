from __future__ import annotations

from typing import Optional


class PowerDNSAPIError(RuntimeError):
    """Raised when the PowerDNS API returns a non-successful response."""

    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        response_text: Optional[str] = None,
    ):
        super().__init__(message)
        self.status = status
        self.response_text = response_text


class MigrationError(RuntimeError):
    """Raised when migration cannot proceed."""
