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
        auto_fix_cname_conflicts: bool = False,
        auto_fix_double_cname_conflicts: bool = False,
    ):
        self.ignore_soa_serial = ignore_soa_serial
        self.auto_fix_cname_conflicts = auto_fix_cname_conflicts
        self.auto_fix_double_cname_conflicts = auto_fix_double_cname_conflicts
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
        source_zone = await self.source_client.get_zone(zone)
        sanitized = self._sanitize_zone(source_zone)
        target_zone = await self.target_client.zone_exists(zone)

        if target_zone:
            changes = self._build_changes(zone, sanitized, target_zone)
            if changes:
                logging.debug(
                    "Pending zone %s rrset changes: %d",
                    zone,
                    len(changes),
                )

                if recreate:
                    logging.debug("Zone %s recreating due to rrset changes", zone)
                    if not dry_run:
                        await self.target_client.delete_zone(zone)
                        created = await self.target_client.create_zone(sanitized)
                    logging.debug("Zone %s recreated on target", zone)
                    return {
                        "source_zone": sanitized,
                        "target_zone": created if not dry_run else {},
                        "changes": changes,
                        "migrator_action": "RECREATE_ZONE",
                    }

                if not dry_run:
                    await self.target_client.patch_zone_rrsets(zone, changes)
                logging.debug("Zone %s patched on target", zone)
                return {
                    "source_zone": sanitized,
                    "target_zone": {},
                    "changes": changes,
                    "migrator_action": "PATCH_ZONE",
                }
            else:
                logging.debug("Zone %s is already in sync", zone)

            return {
                "source_zone": sanitized,
                "target_zone": target_zone if not dry_run else {},
                "changes": {},
                "migrator_action": "NOOP",
            }

        if not dry_run:
            created = await self.target_client.create_zone(sanitized)
        logging.debug("Zone %s created on target", zone)
        return {
            "source_zone": sanitized,
            "target_zone": created if not dry_run else {},
            "changes": {},
            "migrator_action": "CREATE_ZONE",
        }

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
        if self.auto_fix_cname_conflicts:
            sanitized["rrsets"] = self._drop_cname_conflicts(
                sanitized["rrsets"], sanitized["name"]
            )
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

    def _drop_cname_conflicts(
        self, rrsets: List[Dict[str, Any]], zone_name: str
    ) -> List[Dict[str, Any]]:
        apex_name = normalize_zone_name(zone_name)
        rrsets_by_name: Dict[str, List[Dict[str, Any]]] = {}
        for rrset in rrsets:
            name = normalize_zone_name(rrset["name"])
            rrsets_by_name.setdefault(name, []).append(rrset)

        cleaned: List[Dict[str, Any]] = []
        for name, grouped in rrsets_by_name.items():
            cname_rrsets = [rr for rr in grouped if rr.get("type") == "CNAME"]
            if self.auto_fix_double_cname_conflicts:
                for rrset in cname_rrsets:
                    records = rrset.get("records", [])
                    if len(records) > 1:
                        removed_records = records[1:]
                        kept_record = records[:1]
                        rrset["records"] = kept_record
                        logging.warning(
                            "Auto-fix: trimming CNAME rrset %s to first record; kept=%s removed=%s",
                            name,
                            [record.get("content", "") for record in kept_record],
                            [record.get("content", "") for record in removed_records],
                        )
            if not cname_rrsets:
                cleaned.extend(grouped)
                continue

            if name == apex_name:
                removed_types = sorted(
                    {rr.get("type", "UNKNOWN") for rr in cname_rrsets}
                )
                removed_records = [
                    record.get("content", "")
                    for rr in cname_rrsets
                    for record in rr.get("records", [])
                ]
                kept_records = [
                    record.get("content", "")
                    for rr in grouped
                    if rr not in cname_rrsets
                    for record in rr.get("records", [])
                ]
                cleaned.extend([rr for rr in grouped if rr not in cname_rrsets])
                logging.warning(
                    "Auto-fix: dropping %s rrsets for apex %s because CNAME is invalid; kept=%s removed=%s",
                    ", ".join(removed_types),
                    name,
                    kept_records,
                    removed_records,
                )
                continue

            if len(grouped) > len(cname_rrsets):
                cleaned.extend(cname_rrsets)
                removed_types = sorted(
                    {
                        rr.get("type", "UNKNOWN")
                        for rr in grouped
                        if rr not in cname_rrsets
                    }
                )
                kept_records = [
                    record.get("content", "")
                    for rr in cname_rrsets
                    for record in rr.get("records", [])
                ]
                removed_records = [
                    record.get("content", "")
                    for rr in grouped
                    if rr not in cname_rrsets
                    for record in rr.get("records", [])
                ]
                logging.warning(
                    "Auto-fix: dropping %s rrsets for %s because CNAME exists; kept=%s removed=%s",
                    ", ".join(removed_types),
                    name,
                    kept_records,
                    removed_records,
                )
            else:
                cleaned.extend(grouped)
        return cleaned

    def _rrset_key(self, rrset: Dict[str, Any]) -> Tuple[str, str]:
        return (normalize_zone_name(rrset["name"]), rrset["type"])

    def _rrset_equal(self, source: Dict[str, Any], target: Dict[str, Any]) -> bool:
        return self._normalize_rrset(source) == self._normalize_rrset(target)

    def _normalize_rrset(self, rrset: Dict[str, Any]) -> Dict[str, Any]:
        records = rrset.get("records", [])
        normalized_records = sorted(
            (
                self._normalize_record_content(
                    rrset.get("type"), record.get("content", "")
                ),
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

    def _build_changes(
        self,
        zone_name: str,
        source_zone: Dict[str, Any],
        target_zone: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
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
                logging.debug(
                    "Pending zone %s rrset update: %s/%s",
                    zone_name,
                    source_rrset["name"],
                    source_rrset["type"],
                )
                logging.debug(
                    "Pending zone %s rrset %s/%s before: %s",
                    zone_name,
                    target_rrset["name"],
                    target_rrset["type"],
                    self._rrset_summary(target_rrset),
                )
                logging.debug(
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
                logging.debug(
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
        return content

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
        self, source_rrset: Dict[str, Any], target_rrset: Dict[str, Any]
    ) -> Dict[str, Any]:
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
