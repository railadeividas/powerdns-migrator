from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path
from typing import Optional

from .async_migrator import AsyncZoneMigrator
from .config import PowerDNSConnection
from .errors import MigrationError, PowerDNSAPIError


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate a PowerDNS zone between two servers."
    )
    parser.add_argument(
        "--source-url",
        required=True,
        help="Source PowerDNS API base URL, e.g. https://pdns:8081",
    )
    parser.add_argument(
        "--source-key", required=True, help="API key for the source server"
    )
    parser.add_argument(
        "--source-server-id",
        default="localhost",
        help="Source server id (default: localhost)",
    )
    parser.add_argument(
        "--target-url", required=True, help="Target PowerDNS API base URL"
    )
    parser.add_argument(
        "--target-key", required=True, help="API key for the target server"
    )
    parser.add_argument(
        "--target-server-id",
        default="localhost",
        help="Target server id (default: localhost)",
    )
    zone_group = parser.add_mutually_exclusive_group(required=True)
    zone_group.add_argument("--zone", help="Zone name (with or without trailing dot)")
    zone_group.add_argument("--zones-file", help="File with zone names, one per line")
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete and recreate the zone if it already exists on target",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and sanitize zone, but do not write to target",
    )
    parser.add_argument(
        "--insecure-source", action="store_true", help="Do not verify TLS for source"
    )
    parser.add_argument(
        "--insecure-target", action="store_true", help="Do not verify TLS for target"
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="Retry count for transient API errors"
    )
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=0.5,
        help="Base backoff seconds between retries",
    )
    parser.add_argument(
        "--retry-max-backoff",
        type=float,
        default=5.0,
        help="Maximum backoff seconds between retries",
    )
    parser.add_argument(
        "--retry-jitter",
        type=float,
        default=0.1,
        help="Max random jitter seconds added to backoff",
    )
    parser.add_argument(
        "--on-error",
        choices=["continue", "stop"],
        default="continue",
        help="Batch behavior on API error: continue or stop (default: continue)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Parallel migrations when using zones-file",
    )
    parser.add_argument(
        "--graceful-timeout",
        type=float,
        default=0.0,
        help="Seconds to wait on Ctrl+C for queued work to finish (0 = wait indefinitely)",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=30.0,
        help="Progress log interval in seconds for batch runs (0 = disable)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set log level",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging (alias for --log-level DEBUG)",
    )
    return parser.parse_args(argv)


def _build_connections(
    args: argparse.Namespace,
) -> tuple[PowerDNSConnection, PowerDNSConnection]:
    source = PowerDNSConnection(
        base_url=args.source_url,
        api_key=args.source_key,
        server_id=args.source_server_id,
        verify_ssl=not args.insecure_source,
    )
    target = PowerDNSConnection(
        base_url=args.target_url,
        api_key=args.target_key,
        server_id=args.target_server_id,
        verify_ssl=not args.insecure_target,
    )
    return source, target


async def _run_single(args: argparse.Namespace) -> int:
    source, target = _build_connections(args)
    migrator = AsyncZoneMigrator(
        source,
        target,
        timeout=args.timeout,
        retries=args.retries,
        retry_backoff=args.retry_backoff,
        retry_max_backoff=args.retry_max_backoff,
        retry_jitter=args.retry_jitter,
    )
    try:
        result = await migrator.migrate(
            args.zone, recreate=args.recreate, dry_run=args.dry_run
        )
    except (PowerDNSAPIError, MigrationError) as exc:
        logging.error("%s", exc)
        return 1
    finally:
        try:
            await asyncio.shield(migrator.close())
        except asyncio.CancelledError:
            pass

    if args.dry_run:
        rrsets_count = len(result.get("rrsets", [])) if isinstance(result, dict) else 0
        logging.info("Dry run complete; target would receive %d rrsets", rrsets_count)
    else:
        logging.info("Zone %s migrated successfully", args.zone)
    return 0


async def _run_batch(args: argparse.Namespace) -> int:
    source, target = _build_connections(args)
    migrator = AsyncZoneMigrator(
        source,
        target,
        timeout=args.timeout,
        retries=args.retries,
        retry_backoff=args.retry_backoff,
        retry_max_backoff=args.retry_max_backoff,
        retry_jitter=args.retry_jitter,
    )
    zones_path = Path(args.zones_file)
    if not zones_path.exists():
        logging.error("Zones file not found: %s", zones_path)
        await asyncio.shield(migrator.close())
        return 1

    success = 0
    failed = 0
    counter_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    start_time = time.monotonic()

    async def worker(queue: asyncio.Queue[str | None]) -> None:
        nonlocal success, failed
        while True:
            zone = await queue.get()
            if zone is None:
                queue.task_done()
                break
            if stop_event.is_set():
                queue.task_done()
                continue
            try:
                logging.debug("Processing zone %s", zone)
                await migrator.migrate(
                    zone, recreate=args.recreate, dry_run=args.dry_run
                )
                async with counter_lock:
                    success += 1
            except (PowerDNSAPIError, MigrationError) as exc:
                logging.error("Zone %s failed: %s", zone, exc)
                async with counter_lock:
                    failed += 1
                if args.on_error == "stop":
                    stop_event.set()
            finally:
                queue.task_done()

    async def progress_logger() -> None:
        if args.progress_interval <= 0:
            return
        while not stop_event.is_set():
            await asyncio.sleep(args.progress_interval)
            async with counter_lock:
                processed = success + failed
                elapsed = time.monotonic() - start_time
                logging.info(
                    "Progress: processed=%d success=%d failed=%d elapsed=%.1fs",
                    processed,
                    success,
                    failed,
                    elapsed,
                )

    queue: asyncio.Queue[str | None] = asyncio.Queue(
        maxsize=max(1, args.concurrency * 2)
    )
    workers = [
        asyncio.create_task(worker(queue)) for _ in range(max(1, args.concurrency))
    ]
    progress_task = asyncio.create_task(progress_logger())
    cancelled_workers = False

    try:
        with zones_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if stop_event.is_set():
                    break
                zone = line.strip()
                if not zone or zone.startswith("#"):
                    continue
                try:
                    await queue.put(zone)
                except asyncio.CancelledError:
                    logging.warning(
                        "Keyboard interrupt received; stopping intake and finishing queued work."
                    )
                    stop_event.set()
                    break
    except KeyboardInterrupt:
        logging.warning(
            "Keyboard interrupt received; stopping intake and finishing queued work."
        )
        stop_event.set()
    except asyncio.CancelledError:
        logging.warning("Interrupted; stopping intake and finishing queued work.")
        stop_event.set()
    finally:
        if stop_event.is_set() and args.on_error == "stop":
            for task in workers:
                task.cancel()
            cancelled_workers = True
            while not queue.empty():
                try:
                    queue.get_nowait()
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break
        for _ in workers:
            await queue.put(None)
        try:
            if cancelled_workers:
                pass
            elif stop_event.is_set() and args.graceful_timeout > 0:
                await asyncio.wait_for(queue.join(), timeout=args.graceful_timeout)
            else:
                await queue.join()
        except asyncio.TimeoutError:
            logging.warning("Graceful timeout reached; cancelling remaining tasks.")
            for task in workers:
                task.cancel()
        except asyncio.CancelledError:
            for task in workers:
                task.cancel()
        for task in workers:
            try:
                await task
            except asyncio.CancelledError:
                pass
        stop_event.set()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass
        try:
            await asyncio.shield(migrator.close())
        except asyncio.CancelledError:
            pass

    logging.info("Batch complete. Success: %d Failed: %d", success, failed)
    return 1 if failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    level_name = args.log_level or ("DEBUG" if args.verbose else "INFO")
    level = getattr(logging, level_name)

    logger = logging.getLogger()
    logger.setLevel(level)
    formatter = logging.Formatter("[%(levelname)s] %(message)s")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(lambda record: record.levelno < logging.CRITICAL)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.CRITICAL)
    stderr_handler.setFormatter(formatter)

    logger.handlers.clear()
    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)

    try:
        if args.zones_file:
            return asyncio.run(_run_batch(args))
        return asyncio.run(_run_single(args))
    except KeyboardInterrupt:
        logging.warning("Interrupted by user.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
