from __future__ import annotations

import asyncio
import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from powerdns_migrator import (
    AsyncPowerDNSClient,
    AsyncZoneMigrator,
    PowerDNSConnection,
    PowerDNSAPIError,
    PowerDNSConnectionError,
)

# ---------------------------------------------------------------------------
# Defaults from environment (populated by docker-compose)
# ---------------------------------------------------------------------------

_SOURCE_URL = os.environ.get("SOURCE_URL", "http://pdns-auth-1:8081")
_SOURCE_KEY = os.environ.get("SOURCE_KEY", "pdns1key")
_TARGET_URL = os.environ.get("TARGET_URL", "http://pdns-auth-2:8081")
_TARGET_KEY = os.environ.get("TARGET_KEY", "pdns2key")

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ConnectionConfig(BaseModel):
    source_url: str = _SOURCE_URL
    source_key: str = _SOURCE_KEY
    source_server_id: str = "localhost"
    target_url: str = _TARGET_URL
    target_key: str = _TARGET_KEY
    target_server_id: str = "localhost"
    verify_ssl: bool = False  # False by default for local docker dev


class ZoneRequest(BaseModel):
    zone_name: str = ""
    server: str = "source"  # "source" | "target"
    config: ConnectionConfig = ConnectionConfig()


class ListZonesRequest(BaseModel):
    server: str = "source"  # "source" | "target"
    config: ConnectionConfig = ConnectionConfig()


class MigrateRequest(BaseModel):
    zone_name: str
    recreate: bool = False
    dry_run: bool = False
    ignore_soa_serial: bool = False
    auto_fix_cname_conflicts: bool = False
    normalize_txt_escapes: bool = False
    timeout: float = 10.0
    retries: int = 3
    retry_backoff: float = 0.5
    retry_max_backoff: float = 5.0
    retry_jitter: float = 0.1
    config: ConnectionConfig = ConnectionConfig()


class CreateZoneRequest(BaseModel):
    zone_payload: Dict[str, Any]
    server: str = "target"
    config: ConnectionConfig = ConnectionConfig()


class PatchZoneRrsetsRequest(BaseModel):
    zone_name: str
    rrsets: List[Dict[str, Any]]
    server: str = "target"
    config: ConnectionConfig = ConnectionConfig()


class CLIRunRequest(BaseModel):
    zone: str = ""
    zones: str = ""  # newline-separated zone names (batch mode)
    dry_run: bool = False
    recreate: bool = False
    ignore_soa_serial: bool = False
    auto_fix_cname_conflicts: bool = False
    auto_fix_double_cname_conflicts: bool = False
    normalize_txt_escapes: bool = False
    on_error: str = "continue"  # "continue" | "stop"
    concurrency: int = 10
    log_level: str = "INFO"
    timeout: float = 10.0
    retries: int = 3
    retry_backoff: float = 0.5
    retry_max_backoff: float = 5.0
    retry_jitter: float = 0.1
    progress_interval: float = 30.0
    graceful_timeout: float = 0.0
    config: ConnectionConfig = ConnectionConfig()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _conn(cfg: ConnectionConfig, side: str) -> PowerDNSConnection:
    if side == "source":
        return PowerDNSConnection(
            base_url=cfg.source_url,
            api_key=cfg.source_key,
            server_id=cfg.source_server_id,
            verify_ssl=cfg.verify_ssl,
        )
    return PowerDNSConnection(
        base_url=cfg.target_url,
        api_key=cfg.target_key,
        server_id=cfg.target_server_id,
        verify_ssl=cfg.verify_ssl,
    )


def _err(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, PowerDNSAPIError):
        return {
            "error": "PowerDNSAPIError",
            "method": exc.method,
            "url": exc.url,
            "status": exc.status,
            "body": exc.body,
        }
    if isinstance(exc, PowerDNSConnectionError):
        return {
            "error": "PowerDNSConnectionError",
            "method": exc.method,
            "url": exc.url,
            "cause": f"{exc.cause.__class__.__name__}: {exc.cause}"
            if exc.cause
            else None,
            "retries_attempted": exc.retries_attempted,
        }
    return {"error": type(exc).__name__, "message": str(exc)}


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="PowerDNS Migrator Dev UI", version="0.1.0")
templates = Jinja2Templates(directory=Path(__file__).parent)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/get-zone")
async def api_get_zone(req: ZoneRequest) -> Dict[str, Any]:
    client = AsyncPowerDNSClient(_conn(req.config, req.server))
    try:
        data = await client.get_zone(req.zone_name)
        return data
    except (PowerDNSAPIError, PowerDNSConnectionError) as exc:
        return _err(exc)
    finally:
        await client.close()


@app.post("/api/zone-exists")
async def api_zone_exists(req: ZoneRequest) -> Any:
    client = AsyncPowerDNSClient(_conn(req.config, req.server))
    try:
        return await client.zone_exists(req.zone_name)
    except (PowerDNSAPIError, PowerDNSConnectionError) as exc:
        return _err(exc)
    finally:
        await client.close()


@app.post("/api/list-zones")
async def api_list_zones(req: ListZonesRequest) -> Any:
    client = AsyncPowerDNSClient(_conn(req.config, req.server))
    try:
        return await client.list_zones()
    except (PowerDNSAPIError, PowerDNSConnectionError) as exc:
        return _err(exc)
    finally:
        await client.close()


@app.post("/api/migrate")
async def api_migrate(req: MigrateRequest) -> Dict[str, Any]:
    migrator = AsyncZoneMigrator(
        source=_conn(req.config, "source"),
        target=_conn(req.config, "target"),
        timeout=req.timeout,
        retries=req.retries,
        retry_backoff=req.retry_backoff,
        retry_max_backoff=req.retry_max_backoff,
        retry_jitter=req.retry_jitter,
        ignore_soa_serial=req.ignore_soa_serial,
        auto_fix_cname_conflicts=req.auto_fix_cname_conflicts,
        normalize_txt_escapes=req.normalize_txt_escapes,
    )
    try:
        result = await migrator.migrate(
            req.zone_name,
            recreate=req.recreate,
            dry_run=req.dry_run,
        )
        return result
    except (PowerDNSAPIError, PowerDNSConnectionError, Exception) as exc:
        return _err(exc)
    finally:
        await migrator.close()


@app.post("/api/delete-zone")
async def api_delete_zone(req: ZoneRequest) -> Dict[str, Any]:
    client = AsyncPowerDNSClient(_conn(req.config, req.server))
    try:
        await client.delete_zone(req.zone_name)
        return {"deleted": req.zone_name}
    except (PowerDNSAPIError, PowerDNSConnectionError) as exc:
        return _err(exc)
    finally:
        await client.close()


@app.post("/api/create-zone")
async def api_create_zone(req: CreateZoneRequest) -> Dict[str, Any]:
    client = AsyncPowerDNSClient(_conn(req.config, req.server))
    try:
        data = await client.create_zone(req.zone_payload)
        return data
    except (PowerDNSAPIError, PowerDNSConnectionError) as exc:
        return _err(exc)
    finally:
        await client.close()


@app.post("/api/patch-zone-rrsets")
async def api_patch_zone_rrsets(req: PatchZoneRrsetsRequest) -> Dict[str, Any]:
    client = AsyncPowerDNSClient(_conn(req.config, req.server))
    try:
        await client.patch_zone_rrsets(req.zone_name, req.rrsets)
        return {"patched": req.zone_name, "rrsets_count": len(req.rrsets)}
    except (PowerDNSAPIError, PowerDNSConnectionError) as exc:
        return _err(exc)
    finally:
        await client.close()


@app.post("/api/cli-run-stream")
async def api_cli_run_stream(req: CLIRunRequest) -> StreamingResponse:
    cfg = req.config
    args = [
        "python",
        "-m",
        "powerdns_migrator",
        "--source-url",
        cfg.source_url,
        "--source-key",
        cfg.source_key,
        "--source-server-id",
        cfg.source_server_id,
        "--target-url",
        cfg.target_url,
        "--target-key",
        cfg.target_key,
        "--target-server-id",
        cfg.target_server_id,
        "--timeout",
        str(req.timeout),
        "--retries",
        str(req.retries),
        "--retry-backoff",
        str(req.retry_backoff),
        "--retry-max-backoff",
        str(req.retry_max_backoff),
        "--retry-jitter",
        str(req.retry_jitter),
        "--progress-interval",
        str(req.progress_interval),
        "--on-error",
        req.on_error,
        "--concurrency",
        str(req.concurrency),
        "--log-level",
        req.log_level,
    ]
    if req.dry_run:
        args.append("--dry-run")
    if req.recreate:
        args.append("--recreate")
    if req.ignore_soa_serial:
        args.append("--ignore-soa-serial")
    if req.auto_fix_cname_conflicts:
        args.append("--auto-fix-cname-conflicts")
    if req.auto_fix_double_cname_conflicts:
        args.append("--auto-fix-double-cname-conflicts")
    if req.normalize_txt_escapes:
        args.append("--normalize-txt-escapes")
    if req.graceful_timeout > 0:
        args.extend(["--graceful-timeout", str(req.graceful_timeout)])

    display_args = [
        "****" if (i > 0 and args[i - 1] in ("--source-key", "--target-key")) else a
        for i, a in enumerate(args)
    ]

    tmp_path: Optional[str] = None
    if req.zone:
        args.extend(["--zone", req.zone])
    elif req.zones:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(req.zones.strip() + "\n")
            tmp_path = f.name
        args.extend(["--zones-file", tmp_path])
    else:

        async def _err_gen() -> AsyncIterator[str]:
            yield (
                json.dumps(
                    {
                        "type": "error",
                        "error": "ValueError",
                        "message": "Provide a zone name or batch zones",
                    }
                )
                + "\n"
            )

        return StreamingResponse(_err_gen(), media_type="application/x-ndjson")

    async def generate() -> AsyncIterator[str]:
        try:
            yield (
                json.dumps({"type": "command", "text": shlex.join(display_args)}) + "\n"
            )

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            q: asyncio.Queue = asyncio.Queue()

            async def pump(stream: asyncio.StreamReader, name: str) -> None:
                async for line in stream:
                    await q.put((name, line.decode(errors="replace")))
                await q.put((name, None))

            asyncio.create_task(pump(proc.stdout, "stdout"))
            asyncio.create_task(pump(proc.stderr, "stderr"))

            done = 0
            while done < 2:
                name, text = await q.get()
                if text is None:
                    done += 1
                    continue
                yield json.dumps({"type": name, "text": text}) + "\n"

            await proc.wait()
            yield json.dumps({"type": "done", "returncode": proc.returncode}) + "\n"
        finally:
            if tmp_path:
                os.unlink(tmp_path)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


# ---------------------------------------------------------------------------
# HTML UI
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def ui(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "source_url": _SOURCE_URL,
            "source_key": _SOURCE_KEY,
            "target_url": _TARGET_URL,
            "target_key": _TARGET_KEY,
        },
    )
