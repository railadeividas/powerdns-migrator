from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, cast

import aiohttp

from .errors import PowerDNSAPIError, PowerDNSConnectionError
from .utils import normalize_zone_name
from .config import PowerDNSConnection


class AsyncPowerDNSClient:
    """Async PowerDNS API helper."""

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

    async def _request_json(
        self, method: str, path: str, **kwargs: Any
    ) -> Dict[str, Any]:
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
                        logging.debug(
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
                    return cast(Dict[str, Any], await resp.json())
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                delay = self._retry_delay(attempt)
                logging.debug(
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
                        logging.debug(
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
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                delay = self._retry_delay(attempt)
                logging.debug(
                    "Retrying %s %s in %.2fs after error: %s", method, url, delay, exc
                )
                await asyncio.sleep(delay)
        raise PowerDNSConnectionError(
            method=method,
            url=url,
            cause=last_error,
            retries_attempted=self.retries,
        )

    async def get_zone(self, zone_name: str) -> Dict[str, Any]:
        zone = normalize_zone_name(zone_name)
        return await self._request_json("GET", f"/zones/{zone}")

    async def zone_exists(self, zone_name: str) -> Dict[str, Any] | None:
        try:
            return await self.get_zone(zone_name)
        except PowerDNSAPIError as exc:
            if exc.status == 404:
                return None
            raise

    async def delete_zone(self, zone_name: str) -> None:
        zone = normalize_zone_name(zone_name)
        await self._request_ok("DELETE", f"/zones/{zone}")

    async def create_zone(self, zone_payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request_json("POST", "/zones", json=zone_payload)

    async def patch_zone_rrsets(
        self, zone_name: str, rrsets: list[Dict[str, Any]]
    ) -> None:
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
