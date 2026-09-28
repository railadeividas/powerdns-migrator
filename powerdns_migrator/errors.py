from __future__ import annotations

import functools
from typing import Any


class PowerDNSMigratorError(Exception):
    """Base exception for all powerdns-migrator errors.

    Catch this to handle any error originating from this package.
    """

    def __reduce__(self) -> tuple[Any, tuple[Any, ...]]:
        # Subclasses take keyword-only constructor args and store each one
        # as a same-named instance attribute, so __dict__ already holds the
        # exact kwargs needed to reconstruct the instance. This makes these
        # exceptions picklable (e.g. for multiprocessing/Celery) without
        # relying on the default Exception.__reduce__, which replays
        # self.args positionally and doesn't match these signatures.
        return (functools.partial(self.__class__, **self.__dict__), ())


class PowerDNSAPIError(PowerDNSMigratorError):
    """The PowerDNS API responded with an HTTP 4xx/5xx status.

    Attributes:
        method: HTTP method of the failed request.
        url: Full request URL.
        status: HTTP status code returned by the API.
        body: Raw response body returned by the API.
        retries_attempted: Number of retries before the final response.
        timeout_retries_attempted: Retries caused by timeouts before the final response.
    """

    def __init__(
        self,
        *,
        method: str,
        url: str,
        status: int,
        body: str = "",
        retries_attempted: int = 0,
        timeout_retries_attempted: int = 0,
    ) -> None:
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        self.retries_attempted = retries_attempted
        self.timeout_retries_attempted = timeout_retries_attempted
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
        timeout_retries_attempted: Retries caused by timeouts before giving up.
    """

    def __init__(
        self,
        *,
        method: str,
        url: str,
        cause: Exception | None = None,
        retries_attempted: int = 0,
        timeout_retries_attempted: int = 0,
    ) -> None:
        self.method = method
        self.url = url
        self.cause = cause
        self.retries_attempted = retries_attempted
        self.timeout_retries_attempted = timeout_retries_attempted
        cause_detail = f"{cause.__class__.__name__}: {cause}" if cause else "unknown"
        super().__init__(
            f"Connection failed: {method} {url} after "
            f"{retries_attempted} retries: {cause_detail}"
        )


class PowerDNSResponseError(PowerDNSMigratorError):
    """The API returned a successful HTTP status with an unusable JSON body.

    Attributes:
        method: HTTP method of the request.
        url: Full request URL.
        detail: Why the response cannot be used.
    """

    def __init__(self, *, method: str, url: str, detail: str) -> None:
        self.method = method
        self.url = url
        self.detail = detail
        super().__init__(f"Invalid PowerDNS response: {method} {url}: {detail}")


class MigratorConfigError(PowerDNSMigratorError):
    """A configuration or validation error in the migrator.

    Raised for problems like missing files, invalid argument values,
    or other pre-flight validation failures.
    """
