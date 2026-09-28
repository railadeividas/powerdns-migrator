from __future__ import annotations

import json
import logging
from typing import Any, Self

from .async_client import AsyncPowerDNSClient
from .config import PowerDNSConnection
from .utils import normalize_zone_name

logger = logging.getLogger(__name__)


class AsyncZoneMigrator:
    """Orchestrates DNS zone migrations between two PowerDNS servers.

    Fetches a zone from a source server, sanitizes it, computes a minimal
    changeset by diffing against the current target state, and applies the
    changes.  Supports dry-run mode, full zone recreation, and several
    optional conflict-resolution strategies.

    Args:
        source: Source PowerDNS connection config or a pre-built
            :class:`~powerdns_migrator.async_client.AsyncPowerDNSClient`.
        target: Target PowerDNS connection config or a pre-built
            :class:`~powerdns_migrator.async_client.AsyncPowerDNSClient`.
        timeout: HTTP request timeout in seconds (default: ``10.0``).
        retries: Retry count for transient API errors (default: ``3``).
        retry_backoff: Base backoff between retries in seconds (default: ``0.5``).
        retry_max_backoff: Maximum backoff in seconds (default: ``5.0``).
        retry_jitter: Maximum random jitter added to backoff (default: ``0.1``).
        retry_create_timeouts: Retry zone creation after timeouts (default: ``False``).
        ignore_soa_serial: When ``True``, the SOA serial is excluded from
            diff comparisons and the target serial is preserved on write.
        normalize_txt_escapes: When ``True``, decimal escape sequences in
            TXT/SPF records (e.g. ``\\239``) are decoded to raw bytes before
            comparison, enabling equivalence detection across backends.
    """

    def __init__(
        self,
        source: PowerDNSConnection | AsyncPowerDNSClient,
        target: PowerDNSConnection | AsyncPowerDNSClient,
        timeout: float = 10.0,
        retries: int = 3,
        retry_backoff: float = 0.5,
        retry_max_backoff: float = 5.0,
        retry_jitter: float = 0.1,
        ignore_soa_serial: bool = False,
        normalize_txt_escapes: bool = False,
        retry_create_timeouts: bool = False,
    ):
        self.ignore_soa_serial = ignore_soa_serial
        self.normalize_txt_escapes = normalize_txt_escapes
        self._owns_source = not isinstance(source, AsyncPowerDNSClient)
        self._owns_target = not isinstance(target, AsyncPowerDNSClient)
        self.source_client = (
            source
            if isinstance(source, AsyncPowerDNSClient)
            else AsyncPowerDNSClient(
                source,
                timeout=timeout,
                retries=retries,
                retry_backoff=retry_backoff,
                retry_max_backoff=retry_max_backoff,
                retry_jitter=retry_jitter,
                retry_create_timeouts=retry_create_timeouts,
            )
        )
        self.target_client = (
            target
            if isinstance(target, AsyncPowerDNSClient)
            else AsyncPowerDNSClient(
                target,
                timeout=timeout,
                retries=retries,
                retry_backoff=retry_backoff,
                retry_max_backoff=retry_max_backoff,
                retry_jitter=retry_jitter,
                retry_create_timeouts=retry_create_timeouts,
            )
        )

    async def close(self) -> None:
        try:
            if self._owns_source:
                await self.source_client.close()
        finally:
            if self._owns_target:
                await self.target_client.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def migrate(
        self, zone_name: str, recreate: bool = False, dry_run: bool = False
    ) -> dict[str, Any]:
        """Migrate a single DNS zone from source to target.

        Fetches the zone from the source server, sanitizes the payload,
        checks whether it exists on the target, and applies the minimal set
        of changes required to bring the target in sync.

        Args:
            zone_name: Zone name to migrate, with or without trailing dot
                (e.g. ``"example.com."`` or ``"example.com"``).
            recreate: When ``True``, delete the zone on the target before
                recreating it from scratch.  When ``False`` (default), only
                changed RRSets are patched.
            dry_run: When ``True``, compute and return the change plan without
                writing anything to the target server.

        Returns:
            A dict with keys ``source_zone`` (sanitized source payload),
            ``target_zone`` (resulting target data), ``changes`` (list of
            RRSet change dicts applied or planned), and ``migrator_action``
            — one of ``CREATE_ZONE``, ``PATCH_ZONE``, ``RECREATE_ZONE``,
            or ``NOOP``.

        Raises:
            PowerDNSAPIError: Source or target API returned an HTTP error.
            PowerDNSConnectionError: Network failure communicating with source
                or target after all retries.
            PowerDNSResponseError: Source or target returned unusable JSON.
        """
        zone = normalize_zone_name(zone_name)
        source_zone = await self.source_client.get_zone(zone)
        sanitized = self._sanitize_zone(source_zone)
        target_zone = await self.target_client.zone_exists(zone)

        if target_zone:
            changes = self._build_changes(zone, sanitized, target_zone)
            if changes:
                logger.debug(
                    "Pending zone %s rrset changes: %d",
                    zone,
                    len(changes),
                )

                if recreate:
                    logger.debug("Zone %s recreating due to rrset changes", zone)
                    if not dry_run:
                        await self.target_client.delete_zone(zone)
                        created = await self.target_client.create_zone(sanitized)
                    logger.debug("Zone %s recreated on target", zone)
                    return {
                        "source_zone": sanitized,
                        "target_zone": created if not dry_run else {},
                        "changes": changes,
                        "migrator_action": "RECREATE_ZONE",
                    }

                if not dry_run:
                    await self.target_client.patch_zone_rrsets(zone, changes)
                logger.debug("Zone %s patched on target", zone)
                return {
                    "source_zone": sanitized,
                    "target_zone": {},
                    "changes": changes,
                    "migrator_action": "PATCH_ZONE",
                }
            else:
                logger.debug("Zone %s is already in sync", zone)

            return {
                "source_zone": sanitized,
                "target_zone": target_zone if not dry_run else {},
                "changes": [],
                "migrator_action": "NOOP",
            }

        if not dry_run:
            created = await self.target_client.create_zone(sanitized)
        logger.debug("Zone %s created on target", zone)
        return {
            "source_zone": sanitized,
            "target_zone": created if not dry_run else {},
            "changes": [],
            "migrator_action": "CREATE_ZONE",
        }

    def _sanitize_zone(self, zone: dict[str, Any]) -> dict[str, Any]:
        keep_keys = {
            "name",
            "kind",
            "masters",
            "nameservers",
            "account",
            "soa_edit",
            "soa_edit_api",
        }
        sanitized: dict[str, Any] = {key: zone[key] for key in keep_keys if key in zone}
        sanitized["name"] = normalize_zone_name(zone["name"])
        sanitized.setdefault("kind", "Native")
        sanitized["rrsets"] = self._sanitize_rrsets(zone.get("rrsets", []))
        return sanitized

    def _sanitize_rrsets(self, rrsets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for rr in rrsets:
            records = [
                {
                    "content": record["content"],
                    "disabled": record.get("disabled", False),
                    **(
                        {"priority": record["priority"]} if "priority" in record else {}
                    ),
                }
                for record in rr.get("records", [])
            ]
            cleaned_rr = {
                "name": normalize_zone_name(rr["name"]),
                "type": rr["type"],
                "ttl": rr.get("ttl", 3600),
                "records": records,
            }
            if rr.get("comments"):
                cleaned_rr["comments"] = rr["comments"]
            cleaned.append(cleaned_rr)
        return cleaned

    def _rrset_key(self, rrset: dict[str, Any]) -> tuple[str, str]:
        return (normalize_zone_name(rrset["name"]), rrset["type"])

    def _rrset_equal(self, source: dict[str, Any], target: dict[str, Any]) -> bool:
        return self._normalize_rrset(source) == self._normalize_rrset(target)

    def _normalize_rrset(self, rrset: dict[str, Any]) -> dict[str, Any]:
        records = rrset.get("records", [])
        normalized_records = [
            (
                self._normalize_record_content(
                    rrset.get("type"), record.get("content", "")
                ),
                bool(record.get("disabled", False)),
                record.get("priority"),
            )
            for record in records
        ]
        normalized_records.sort(key=json.dumps)
        comments = rrset.get("comments") or []
        normalized_comments = [
            (
                comment.get("content", ""),
                bool(comment.get("disabled", False)),
                comment.get("account"),
                comment.get("modified_at"),
            )
            for comment in comments
        ]
        normalized_comments.sort(key=json.dumps)
        return {
            "name": normalize_zone_name(rrset["name"]),
            "type": rrset["type"],
            "ttl": rrset.get("ttl"),
            "records": normalized_records,
            "comments": normalized_comments,
        }

    def _rrset_change(self, changetype: str, rrset: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "name": normalize_zone_name(rrset["name"]),
            "type": rrset["type"],
            "changetype": changetype,
            "ttl": rrset.get("ttl", 3600),
            "records": rrset.get("records", []),
        }
        if rrset.get("comments"):
            payload["comments"] = rrset["comments"]
        return payload

    def _build_changes(
        self,
        zone_name: str,
        source_zone: dict[str, Any],
        target_zone: dict[str, Any],
    ) -> list[dict[str, Any]]:
        source_rrsets = {
            self._rrset_key(rr): rr for rr in source_zone.get("rrsets", [])
        }
        target_rrsets = {
            self._rrset_key(rr): rr for rr in target_zone.get("rrsets", [])
        }

        deletes: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        creates: list[dict[str, Any]] = []

        for key, target_rrset in target_rrsets.items():
            if key not in source_rrsets:
                logger.debug(
                    "Pending zone %s rrset deletion: %s/%s",
                    zone_name,
                    target_rrset["name"],
                    target_rrset["type"],
                )
                deletes.append(self._rrset_change("DELETE", target_rrset))

        for key, source_rrset in source_rrsets.items():
            target_rrset = target_rrsets.get(key)
            if target_rrset is None:
                continue
            if not self._rrset_equal(source_rrset, target_rrset):
                logger.debug(
                    "Pending zone %s rrset update: %s/%s",
                    zone_name,
                    source_rrset["name"],
                    source_rrset["type"],
                )
                logger.debug(
                    "Pending zone %s rrset %s/%s before: %s",
                    zone_name,
                    target_rrset["name"],
                    target_rrset["type"],
                    self._rrset_summary(target_rrset),
                )
                logger.debug(
                    "Pending zone %s rrset %s/%s after: %s",
                    zone_name,
                    source_rrset["name"],
                    source_rrset["type"],
                    self._rrset_summary(source_rrset),
                )
                if self.ignore_soa_serial and source_rrset["type"] == "SOA":
                    source_rrset = self._preserve_target_soa_serial(
                        source_rrset, target_rrset
                    )
                updates.append(self._rrset_change("REPLACE", source_rrset))

        for key, source_rrset in source_rrsets.items():
            if key not in target_rrsets:
                logger.debug(
                    "Pending zone %s rrset creation: %s/%s",
                    zone_name,
                    source_rrset["name"],
                    source_rrset["type"],
                )
                creates.append(self._rrset_change("REPLACE", source_rrset))

        return deletes + updates + creates

    def _normalize_record_content(self, rrtype: str | None, content: str) -> str:
        if self.ignore_soa_serial and rrtype == "SOA":
            return self._normalize_soa_content(content, serial_override="0")
        if self.normalize_txt_escapes and rrtype in {"TXT", "SPF"}:
            return self._decode_decimal_escapes(content)
        return content

    def _decode_decimal_escapes(self, content: str) -> str:
        """Decode decimal escape sequences (e.g. \\239\\191\\189) to raw bytes.

        PowerDNS backends may represent the same binary content differently:
        - As raw UTF-8 bytes (e.g. the actual replacement character)
        - As escaped decimal sequences (e.g. \\239\\191\\189 per RFC 1035)

        This normalizes both representations to raw bytes for comparison.
        We convert the string to bytes, decode escape sequences, then re-decode as UTF-8.
        """
        # Convert string to bytes (each char as its byte value in latin-1)
        # This preserves raw bytes that are already in the string
        result_bytes = bytearray()
        i = 0
        while i < len(content):
            if content[i] == "\\" and i + 3 < len(content):
                # Check if next 3 chars are decimal digits
                maybe_decimal = content[i + 1 : i + 4]
                if maybe_decimal.isdigit():
                    byte_val = int(maybe_decimal, 10)
                    if byte_val <= 255:
                        result_bytes.append(byte_val)
                        i += 4
                        continue
            # Encode the character as UTF-8 bytes
            result_bytes.extend(content[i].encode("utf-8"))
            i += 1

        # Decode back to string as UTF-8, replacing invalid sequences
        return result_bytes.decode("utf-8", errors="replace")

    def _normalize_soa_content(
        self, content: str, serial_override: str | None = None
    ) -> str:
        parts = content.split()
        if len(parts) < 7:
            return content
        if serial_override is not None:
            parts[2] = serial_override
        return " ".join(parts)

    def _preserve_target_soa_serial(
        self, source_rrset: dict[str, Any], target_rrset: dict[str, Any]
    ) -> dict[str, Any]:
        target_records = target_rrset.get("records", [])
        if not target_records:
            return source_rrset
        target_content = target_records[0].get("content", "")
        target_parts = target_content.split()
        if len(target_parts) < 7:
            return source_rrset
        target_serial = target_parts[2]
        updated = dict(source_rrset)
        updated_records = []
        for record in source_rrset.get("records", []):
            content = record.get("content", "")
            new_content = self._normalize_soa_content(
                content, serial_override=target_serial
            )
            updated_record = dict(record)
            updated_record["content"] = new_content
            updated_records.append(updated_record)
        updated["records"] = updated_records
        return updated

    def _rrset_summary(self, rrset: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": normalize_zone_name(rrset["name"]),
            "type": rrset["type"],
            "ttl": rrset.get("ttl"),
            "records": [
                {
                    "content": record.get("content", ""),
                    "disabled": bool(record.get("disabled", False)),
                    **(
                        {"priority": record["priority"]} if "priority" in record else {}
                    ),
                }
                for record in rrset.get("records", [])
            ],
            "comments": rrset.get("comments") or [],
        }
