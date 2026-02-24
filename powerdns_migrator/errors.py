from __future__ import annotations


class PowerDNSMigratorError(Exception):
    """Base exception for all powerdns-migrator errors.

    Catch this to handle any error originating from this package.
    """


class PowerDNSAPIError(PowerDNSMigratorError):
    """The PowerDNS API responded with an HTTP 4xx/5xx status.

    Attributes:
        method: HTTP method of the failed request.
        url: Full request URL.
        status: HTTP status code returned by the API.
        body: Raw response body returned by the API.
    """

    def __init__(
        self,
        *,
        method: str,
        url: str,
        status: int,
        body: str = "",
    ) -> None:
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(
            f"PowerDNS API error: {method} {url} returned {status}: {body}"
        )


class PowerDNSConnectionError(PowerDNSMigratorError):
    """A network-level failure prevented the request from completing.

    Raised after all retry attempts are exhausted due to connection errors,
    timeouts, DNS resolution failures, or similar transport issues.

    Attributes:
        method: HTTP method of the failed request.
        url: Full request URL.
        cause: The underlying exception that triggered the failure.
        retries_attempted: Number of retries performed before giving up.
    """

    def __init__(
        self,
        *,
        method: str,
        url: str,
        cause: Exception | None = None,
        retries_attempted: int = 0,
    ) -> None:
        self.method = method
        self.url = url
        self.cause = cause
        self.retries_attempted = retries_attempted
        cause_detail = f"{cause.__class__.__name__}: {cause}" if cause else "unknown"
        super().__init__(
            f"Connection failed: {method} {url} after "
            f"{retries_attempted} retries: {cause_detail}"
        )


class MigratorConfigError(PowerDNSMigratorError):
    """A configuration or validation error in the migrator.

    Raised for problems like missing files, invalid argument values,
    or other pre-flight validation failures.
    """
