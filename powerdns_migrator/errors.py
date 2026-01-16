from __future__ import annotations

from typing import Optional


class PowerDNSAPIError(RuntimeError):
    """
    Exception raised when a PowerDNS API request returns a non-successful response.

    Attributes:
        method: HTTP method used for the request, if available.
        url: Full request URL, if available.
        status: HTTP status code returned by the API, if available.
        error_reason: Error reason returned by migrator, if available.
        error_message: Error message returned by the API, if available.
    """

    def __init__(
        self,
        method: Optional[str] = None,
        url: Optional[str] = None,
        status: Optional[int] = None,
        error_reason: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        details = []

        if method:
            details.append(f"method={method}")

        if url:
            details.append(f"url={url}")

        if status is not None:
            details.append(f"status={status}")

        if error_reason:
            details.append(f"error_reason={error_reason}")

        if error_message:
            details.append(f"error_message={error_message}")

        full_message = (
            f"PowerDNS API call error: {', '.join(details)}"
            if details
            else "Unknown error"
        )

        super().__init__(full_message)
        self.method = method
        self.url = url
        self.status = status
        self.error_reason = error_reason
        self.error_message = error_message
