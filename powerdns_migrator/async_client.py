from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, cast

import aiohttp

from .config import PowerDNSConnection
from .errors import PowerDNSAPIError, PowerDNSConnectionError
from .utils import normalize_zone_name

logger = logging.getLogger(__name__)


class AsyncPowerDNSClient:
    """Async HTTP client for the PowerDNS Authoritative API.

    Wraps ``aiohttp`` to provide typed methods for every API operation used
    during zone migration.  All network errors are converted to either
    :class:`~powerdns_migrator.errors.PowerDNSAPIError` (HTTP 4xx/5xx) or
    :class:`~powerdns_migrator.errors.PowerDNSConnectionError` (transport
    failure) after the configured number of retries is exhausted.

    Args:
        connection: Connection configuration (URL, API key, server ID, SSL).
        timeout: HTTP request timeout in seconds (default: ``10.0``).
        retries: Number of retry attempts for transient failures (default: ``3``).
        retry_backoff: Base backoff duration in seconds (default: ``0.5``).
        retry_max_backoff: Maximum backoff duration in seconds (default: ``5.0``).
        retry_jitter: Maximum random jitter added to backoff in seconds (default: ``0.1``).
    """

    def __init__(
        self,
        connection: PowerDNSConnection,
        timeout: float = 10.0,
        retries: int = 3,
        retry_backoff: float = 0.5,
        retry_max_backoff: float = 5.0,
        retry_jitter: float = 0.1,
    ):
        self.connection = connection
        self.timeout = timeout
        self.retries = max(0, retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self.retry_max_backoff = max(0.0, retry_max_backoff)
        self.retry_jitter = max(0.0, retry_jitter)
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
                        )
                    return await resp.json()
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
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
            retries_attempted=self.retries,
        )

    async def _request_ok(self, method: str, path: str, **kwargs: Any) -> None:
        url = self.connection.endpoint(path)
        last_error: Exception | None = None
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
                        )
                    await resp.release()
                    return
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
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
        )

    async def list_zones(self) -> list[dict[str, Any]]:
        """List all zones on the server.

        Returns:
            List of zone summary dicts as returned by ``GET /zones``.

        Raises:
            PowerDNSAPIError: Server responded with 4xx/5xx.
            PowerDNSConnectionError: Network failure after all retries.
        """
        return cast(list[dict[str, Any]], await self._request_json("GET", "/zones"))

    async def get_zone(self, zone_name: str) -> dict[str, Any]:
        """Fetch a zone including all RRSets.

        Args:
            zone_name: Zone name with or without trailing dot.

        Returns:
            Full zone dict including ``rrsets`` as returned by ``GET /zones/{zone}``.

        Raises:
            PowerDNSAPIError: Server responded with 4xx/5xx (including 404 if not found).
            PowerDNSConnectionError: Network failure after all retries.
        """
        zone = normalize_zone_name(zone_name)
        return cast(dict[str, Any], await self._request_json("GET", f"/zones/{zone}"))

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
        """
        return cast(
            dict[str, Any],
            await self._request_json("POST", "/zones", json=zone_payload),
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
