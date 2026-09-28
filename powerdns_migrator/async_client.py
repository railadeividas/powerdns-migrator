from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, cast

import aiohttp

from .config import PowerDNSConnection
from .errors import PowerDNSAPIError, PowerDNSConnectionError, PowerDNSResponseError
from .utils import normalize_zone_name

logger = logging.getLogger(__name__)


class AsyncPowerDNSClient:
    """Async HTTP client for the PowerDNS Authoritative API.

    Wraps ``aiohttp`` to provide typed methods for every API operation used
    during zone migration.  HTTP failures raise
    :class:`~powerdns_migrator.errors.PowerDNSAPIError`, transport failures
    raise :class:`~powerdns_migrator.errors.PowerDNSConnectionError`, and
    unusable successful responses raise
    :class:`~powerdns_migrator.errors.PowerDNSResponseError`.  Transient HTTP
    statuses are retried, including for POST. A POST timeout is retried only
    when ``retry_create_timeouts`` is enabled, because the first attempt may
    have created the zone.

    Args:
        connection: Connection configuration (URL, API key, server ID, SSL).
        timeout: HTTP request timeout in seconds (default: ``10.0``).
        retries: Number of retry attempts for transient failures (default: ``3``).
        retry_backoff: Base backoff duration in seconds (default: ``0.5``).
        retry_max_backoff: Maximum backoff duration in seconds (default: ``5.0``).
        retry_jitter: Maximum random jitter added to backoff in seconds (default: ``0.1``).
        retry_create_timeouts: Retry POST /zones after timeouts (default: ``False``).
    """

    def __init__(
        self,
        connection: PowerDNSConnection,
        timeout: float = 10.0,
        retries: int = 3,
        retry_backoff: float = 0.5,
        retry_max_backoff: float = 5.0,
        retry_jitter: float = 0.1,
        retry_create_timeouts: bool = False,
    ):
        self.connection = connection
        self.timeout = timeout
        self.retries = max(0, retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self.retry_max_backoff = max(0.0, retry_max_backoff)
        self.retry_jitter = max(0.0, retry_jitter)
        self.retry_create_timeouts = retry_create_timeouts
        connector = aiohttp.TCPConnector(ssl=connection.verify_ssl)
        self.client = aiohttp.ClientSession(
            connector=connector,
            headers={
                "X-API-Key": connection.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
        )

    async def close(self) -> None:
        await self.client.close()

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        url = self.connection.endpoint(path)
        last_error: Exception | None = None
        timeout_retries_attempted = 0
        for attempt in range(self.retries + 1):
            try:
                async with self.client.request(method, url, **kwargs) as resp:
                    if self._should_retry_status(resp.status) and attempt < self.retries:
                        delay = self._retry_delay(attempt, resp)
                        logger.debug(
                            "Retrying %s %s in %.2fs (attempt %d/%d)",
                            method,
                            url,
                            delay,
                            attempt + 1,
                            self.retries,
                        )
                        await resp.release()
                        await asyncio.sleep(delay)
                        continue
                    if resp.status >= 400:
                        body = await resp.text()
                        raise PowerDNSAPIError(
                            method=method,
                            url=url,
                            status=resp.status,
                            body=body,
                            retries_attempted=attempt,
                            timeout_retries_attempted=timeout_retries_attempted,
                        )
                    try:
                        return await resp.json()
                    except (
                        aiohttp.ContentTypeError,
                        json.JSONDecodeError,
                        UnicodeDecodeError,
                    ) as exc:
                        raise PowerDNSResponseError(
                            method=method,
                            url=url,
                            detail="expected a JSON response",
                        ) from exc
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_error = exc
                is_create_timeout = method == "POST" and isinstance(
                    exc, (TimeoutError, asyncio.TimeoutError)
                )
                if attempt >= self.retries or (
                    method == "POST"
                    and not (is_create_timeout and self.retry_create_timeouts)
                ):
                    break
                if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                    timeout_retries_attempted += 1
                delay = self._retry_delay(attempt)
                logger.debug(
                    "Retrying %s %s in %.2fs (attempt %d/%d) after error: %s",
                    method,
                    url,
                    delay,
                    attempt + 1,
                    self.retries,
                    exc,
                )
                await asyncio.sleep(delay)
        raise PowerDNSConnectionError(
            method=method,
            url=url,
            cause=last_error,
            retries_attempted=attempt,
            timeout_retries_attempted=timeout_retries_attempted,
        ) from last_error

    async def _request_ok(self, method: str, path: str, **kwargs: Any) -> None:
        url = self.connection.endpoint(path)
        last_error: Exception | None = None
        timeout_retries_attempted = 0
        for attempt in range(self.retries + 1):
            try:
                async with self.client.request(method, url, **kwargs) as resp:
                    if (
                        self._should_retry_status(resp.status)
                        and attempt < self.retries
                    ):
                        delay = self._retry_delay(attempt, resp)
                        logger.debug(
                            "Retrying %s %s in %.2fs (attempt %d/%d)",
                            method,
                            url,
                            delay,
                            attempt + 1,
                            self.retries,
                        )
                        await resp.release()
                        await asyncio.sleep(delay)
                        continue
                    if resp.status >= 400:
                        body = await resp.text()
                        raise PowerDNSAPIError(
                            method=method,
                            url=url,
                            status=resp.status,
                            body=body,
                            retries_attempted=attempt,
                            timeout_retries_attempted=timeout_retries_attempted,
                        )
                    await resp.release()
                    return
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                    timeout_retries_attempted += 1
                delay = self._retry_delay(attempt)
                logger.debug(
                    "Retrying %s %s in %.2fs after error: %s", method, url, delay, exc
                )
                await asyncio.sleep(delay)
        raise PowerDNSConnectionError(
            method=method,
            url=url,
            cause=last_error,
            retries_attempted=self.retries,
            timeout_retries_attempted=timeout_retries_attempted,
        ) from last_error

    def _expect_json_type(
        self, value: Any, expected: type, method: str, path: str
    ) -> Any:
        if not isinstance(value, expected):
            raise PowerDNSResponseError(
                method=method,
                url=self.connection.endpoint(path),
                detail=f"expected a JSON {expected.__name__}",
            )
        return value

    def _expect_json_type(
        self, value: Any, expected: type, method: str, path: str
    ) -> Any:
        if not isinstance(value, expected):
            raise PowerDNSResponseError(
                method=method,
                url=self.connection.endpoint(path),
                detail=f"expected a JSON {expected.__name__}",
            )
        return value

    async def list_zones(self) -> list[dict[str, Any]]:
        """List all zones on the server.

        Returns:
            List of zone summary dicts as returned by ``GET /zones``.

        Raises:
            PowerDNSAPIError: Server responded with 4xx/5xx.
            PowerDNSConnectionError: Network failure after all retries.
            PowerDNSResponseError: Server returned unusable JSON.
        """
        result = await self._request_json("GET", "/zones")
        zones = self._expect_json_type(result, list, "GET", "/zones")
        if any(not isinstance(zone, dict) for zone in zones):
            raise PowerDNSResponseError(
                method="GET",
                url=self.connection.endpoint("/zones"),
                detail="zone list contains an invalid entry",
            )
        return cast(list[dict[str, Any]], zones)

    async def get_zone(self, zone_name: str) -> dict[str, Any]:
        """Fetch a zone including all RRSets.

        Args:
            zone_name: Zone name with or without trailing dot.

        Returns:
            Full zone dict including ``rrsets`` as returned by ``GET /zones/{zone}``.

        Raises:
            PowerDNSAPIError: Server responded with 4xx/5xx (including 404 if not found).
            PowerDNSConnectionError: Network failure after all retries.
            PowerDNSResponseError: Server returned unusable JSON.
        """
        zone = normalize_zone_name(zone_name)
        path = f"/zones/{zone}"
        result = self._expect_json_type(
            await self._request_json("GET", path), dict, "GET", path
        )
        rrsets = result.get("rrsets")
        if not isinstance(result.get("name"), str) or not isinstance(rrsets, list):
            raise PowerDNSResponseError(
                method="GET",
                url=self.connection.endpoint(path),
                detail="zone must contain a name and an rrsets list",
            )
        for rrset in rrsets:
            if (
                not isinstance(rrset, dict)
                or not isinstance(rrset.get("name"), str)
                or not isinstance(rrset.get("type"), str)
                or not isinstance(rrset.get("records"), list)
            ):
                raise PowerDNSResponseError(
                    method="GET",
                    url=self.connection.endpoint(path),
                    detail="zone contains an invalid RRSet",
                )
            for record in rrset.get("records", []):
                if not isinstance(record, dict) or not isinstance(
                    record.get("content"), str
                ):
                    raise PowerDNSResponseError(
                        method="GET",
                        url=self.connection.endpoint(path),
                        detail="zone contains an invalid record",
                    )
            comments = rrset.get("comments", [])
            if comments is not None and (
                not isinstance(comments, list)
                or any(not isinstance(comment, dict) for comment in comments)
            ):
                raise PowerDNSResponseError(
                    method="GET",
                    url=self.connection.endpoint(path),
                    detail="zone contains invalid RRSet comments",
                )
        return cast(dict[str, Any], result)

    async def zone_exists(self, zone_name: str) -> dict[str, Any] | None:
        """Fetch a zone, returning ``None`` if it does not exist.

        Args:
            zone_name: Zone name with or without trailing dot.

        Returns:
            Full zone dict if the zone exists, ``None`` if the server returns 404.

        Raises:
            PowerDNSAPIError: Server responded with a non-404 error status.
            PowerDNSConnectionError: Network failure after all retries.
        """
        try:
            return await self.get_zone(zone_name)
        except PowerDNSAPIError as exc:
            if exc.status == 404:
                return None
            raise

    async def delete_zone(self, zone_name: str) -> None:
        """Delete a zone from the server.

        Args:
            zone_name: Zone name with or without trailing dot.

        Raises:
            PowerDNSAPIError: Server responded with 4xx/5xx.
            PowerDNSConnectionError: Network failure after all retries.
        """
        zone = normalize_zone_name(zone_name)
        await self._request_ok("DELETE", f"/zones/{zone}")

    async def create_zone(self, zone_payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new zone on the server.

        Args:
            zone_payload: Zone creation payload as a dict.  Must include at
                minimum ``name`` and ``kind``; typically also includes ``rrsets``.

        Returns:
            The created zone dict as returned by ``POST /zones``.

        Raises:
            PowerDNSAPIError: Server responded with 4xx/5xx.
            PowerDNSConnectionError: Network failure after all retries.
            PowerDNSResponseError: Server returned unusable JSON.
        """
        result = await self._request_json("POST", "/zones", json=zone_payload)
        return cast(
            dict[str, Any], self._expect_json_type(result, dict, "POST", "/zones")
        )

    async def patch_zone_rrsets(
        self, zone_name: str, rrsets: list[dict[str, Any]]
    ) -> None:
        """Apply RRSet changes to an existing zone.

        Args:
            zone_name: Zone name with or without trailing dot.
            rrsets: List of RRSet change dicts.  Each entry must include a
                ``changetype`` field (``"REPLACE"`` or ``"DELETE"``).

        Raises:
            PowerDNSAPIError: Server responded with 4xx/5xx.
            PowerDNSConnectionError: Network failure after all retries.
        """
        zone = normalize_zone_name(zone_name)
        payload = {"rrsets": rrsets}
        await self._request_ok("PATCH", f"/zones/{zone}", json=payload)

    def _should_retry_status(self, status: int) -> bool:
        return status in {408, 429, 500, 502, 503, 504}

    def _retry_delay(
        self, attempt: int, resp: aiohttp.ClientResponse | None = None
    ) -> float:
        delay: float = min(self.retry_max_backoff, self.retry_backoff * (2**attempt))
        if self.retry_jitter > 0:
            delay += random.uniform(0, self.retry_jitter)  # nosec B311
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = max(delay, float(retry_after))
        return float(delay)
