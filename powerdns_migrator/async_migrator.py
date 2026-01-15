from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from .async_client import AsyncPowerDNSClient
from .config import PowerDNSConnection
from .utils import normalize_zone_name


class AsyncZoneMigrator:
    """Async zone migrator with rrset diffing for existing target zones."""

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
    ):
        self.ignore_soa_serial = ignore_soa_serial
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
            )
        )

    async def close(self) -> None:
        if isinstance(self.source_client, AsyncPowerDNSClient):
            await self.source_client.close()
        if isinstance(self.target_client, AsyncPowerDNSClient):
            await self.target_client.close()

    async def migrate(
        self, zone_name: str, recreate: bool = False, dry_run: bool = False
    ) -> Dict[str, Any]:
        zone = normalize_zone_name(zone_name)
        logging.debug("Starting migration for zone %s", zone)
        source_zone = await self.source_client.get_zone(zone)
        sanitized = self._sanitize_zone(source_zone)

        if dry_run:
            logging.debug("Dry run: skipping writes for zone %s", zone)
            return sanitized

        if await self.target_client.zone_exists(zone):
            if recreate:
                logging.debug("Zone %s exists on target; overwriting", zone)
                await self.target_client.delete_zone(zone)
                created = await self.target_client.create_zone(sanitized)
                logging.debug("Zone %s recreated on target", zone)
                return created
            return await self._sync_existing_zone(zone, sanitized)

        created = await self.target_client.create_zone(sanitized)
        logging.debug("Zone %s created on target", zone)
        return created

    async def _sync_existing_zone(
        self, zone_name: str, source_zone: Dict[str, Any]
    ) -> Dict[str, Any]:
        target_zone = await self.target_client.get_zone(zone_name)
        source_rrsets = {
            self._rrset_key(rr): rr for rr in source_zone.get("rrsets", [])
        }
        target_rrsets = {
            self._rrset_key(rr): rr for rr in target_zone.get("rrsets", [])
        }

        deletes: List[Dict[str, Any]] = []
        updates: List[Dict[str, Any]] = []
        creates: List[Dict[str, Any]] = []

        for key, target_rrset in target_rrsets.items():
            if key not in source_rrsets:
                logging.debug(
                    "Zone %s deleting rrset %s/%s",
                    zone_name,
                    target_rrset["name"],
                    target_rrset["type"],
                )
                deletes.append(self._rrset_change("DELETE", target_rrset))

        for key, source_rrset in source_rrsets.items():
            target_rrset = target_rrsets.get(key)
            if target_rrset is None:
                logging.debug(
                    "Zone %s adding rrset %s/%s",
                    zone_name,
                    source_rrset["name"],
                    source_rrset["type"],
                )
                creates.append(self._rrset_change("REPLACE", source_rrset))
                continue
            if not self._rrset_equal(source_rrset, target_rrset):
                logging.debug(
                    "Zone %s updating rrset %s/%s",
                    zone_name,
                    source_rrset["name"],
                    source_rrset["type"],
                )
                logging.debug(
                    "Zone %s rrset %s/%s before: %s",
                    zone_name,
                    target_rrset["name"],
                    target_rrset["type"],
                    self._rrset_summary(target_rrset),
                )
                logging.debug(
                    "Zone %s rrset %s/%s after: %s",
                    zone_name,
                    source_rrset["name"],
                    source_rrset["type"],
                    self._rrset_summary(source_rrset),
                )
                if self.ignore_soa_serial and source_rrset["type"] == "SOA":
                    source_rrset = self._preserve_target_soa_serial(source_rrset, target_rrset)
                updates.append(self._rrset_change("REPLACE", source_rrset))

        changes = deletes + creates + updates
        if changes:
            logging.debug(
                "Zone %s rrset changes: %d",
                zone_name,
                len(changes),
            )
            await self.target_client.patch_zone_rrsets(zone_name, changes)
        else:
            logging.debug("Zone %s is already in sync", zone_name)

        return await self.target_client.get_zone(zone_name)

    def _sanitize_zone(self, zone: Dict[str, Any]) -> Dict[str, Any]:
        keep_keys = {
            "name",
            "kind",
            "masters",
            "nameservers",
            "account",
            "soa_edit",
            "soa_edit_api",
        }
        sanitized: Dict[str, Any] = {key: zone[key] for key in keep_keys if key in zone}
        sanitized["name"] = normalize_zone_name(zone["name"])
        sanitized.setdefault("kind", "Native")
        sanitized["rrsets"] = self._sanitize_rrsets(zone.get("rrsets", []))
        return sanitized

    def _sanitize_rrsets(self, rrsets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cleaned: List[Dict[str, Any]] = []
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

    def _rrset_key(self, rrset: Dict[str, Any]) -> Tuple[str, str]:
        return (normalize_zone_name(rrset["name"]), rrset["type"])

    def _rrset_equal(self, source: Dict[str, Any], target: Dict[str, Any]) -> bool:
        return self._normalize_rrset(source) == self._normalize_rrset(target)

    def _normalize_rrset(self, rrset: Dict[str, Any]) -> Dict[str, Any]:
        records = rrset.get("records", [])
        normalized_records = sorted(
            (
                self._normalize_record_content(rrset.get("type"), record.get("content", "")),
                bool(record.get("disabled", False)),
                record.get("priority"),
            )
            for record in records
        )
        comments = rrset.get("comments") or []
        normalized_comments = sorted(
            (
                comment.get("content", ""),
                bool(comment.get("disabled", False)),
                comment.get("account"),
                comment.get("modified_at"),
            )
            for comment in comments
        )
        return {
            "name": normalize_zone_name(rrset["name"]),
            "type": rrset["type"],
            "ttl": rrset.get("ttl"),
            "records": normalized_records,
            "comments": normalized_comments,
        }

    def _rrset_change(self, changetype: str, rrset: Dict[str, Any]) -> Dict[str, Any]:
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

    def _normalize_record_content(self, rrtype: str | None, content: str) -> str:
        if self.ignore_soa_serial and rrtype == "SOA":
            return self._normalize_soa_content(content, serial_override="0")
        return content

    def _normalize_soa_content(self, content: str, serial_override: str | None = None) -> str:
        parts = content.split()
        if len(parts) < 7:
            return content
        if serial_override is not None:
            parts[2] = serial_override
        return " ".join(parts)

    def _preserve_target_soa_serial(self, source_rrset: Dict[str, Any], target_rrset: Dict[str, Any]) -> Dict[str, Any]:
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
            new_content = self._normalize_soa_content(content, serial_override=target_serial)
            updated_record = dict(record)
            updated_record["content"] = new_content
            updated_records.append(updated_record)
        updated["records"] = updated_records
        return updated

    def _rrset_summary(self, rrset: Dict[str, Any]) -> Dict[str, Any]:
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
