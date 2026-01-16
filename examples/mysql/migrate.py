#!/usr/bin/env python3
# Read zones from MySQL domains table and migrate them
# pip install powerdns-migrator aiomysql

import asyncio
import os
import logging
import time
from dataclasses import dataclass, field

import aiomysql

from powerdns_migrator.async_migrator import AsyncZoneMigrator
from powerdns_migrator.config import PowerDNSConnection
from powerdns_migrator.errors import PowerDNSAPIError

# MySQL configuration
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "pdns1")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "pdns1pass")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "pdns1")

# Source PowerDNS configuration
SOURCE_URL = os.getenv("SOURCE_URL", "http://localhost:8081")
SOURCE_KEY = os.getenv("SOURCE_KEY", "pdns1key")
SOURCE_SERVER_ID = os.getenv("SOURCE_SERVER_ID", "localhost")
SOURCE_INSECURE = os.getenv("SOURCE_INSECURE", "false").lower() == "true"

# Target PowerDNS configuration
TARGET_URL = os.getenv("TARGET_URL", "http://localhost:8082")
TARGET_KEY = os.getenv("TARGET_KEY", "pdns2key")
TARGET_SERVER_ID = os.getenv("TARGET_SERVER_ID", "localhost")
TARGET_INSECURE = os.getenv("TARGET_INSECURE", "false").lower() == "true"

# Migration behavior
RECREATE = os.getenv("RECREATE", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
CONCURRENCY = int(os.getenv("CONCURRENCY", "5"))
ON_ERROR = os.getenv("ON_ERROR", "continue")  # continue or stop

# Batch configuration - fetch domains in chunks to avoid memory issues
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5000"))
QUEUE_SIZE = int(os.getenv("QUEUE_SIZE", "1000"))  # Max zones waiting in queue

# Progress reporting
PROGRESS_INTERVAL = int(os.getenv("PROGRESS_INTERVAL", "5"))  # seconds

# HTTP client configuration
TIMEOUT = float(os.getenv("TIMEOUT", "10"))
RETRIES = int(os.getenv("RETRIES", "3"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "0.5"))
RETRY_MAX_BACKOFF = float(os.getenv("RETRY_MAX_BACKOFF", "5.0"))
RETRY_JITTER = float(os.getenv("RETRY_JITTER", "0.1"))

# Zone migration options
IGNORE_SOA_SERIAL = os.getenv("IGNORE_SOA_SERIAL", "true").lower() == "true"
AUTO_FIX_CNAME_CONFLICTS = (
    os.getenv("AUTO_FIX_CNAME_CONFLICTS", "true").lower() == "true"
)
AUTO_FIX_DOUBLE_CNAME_CONFLICTS = (
    os.getenv("AUTO_FIX_DOUBLE_CNAME_CONFLICTS", "true").lower() == "true"
)
NORMALIZE_TXT_ESCAPES = os.getenv("NORMALIZE_TXT_ESCAPES", "true").lower() == "true"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


@dataclass
class MigrationStats:
    total: int = 0
    processed: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    start_time: float = field(default_factory=time.time)
    stop_requested: bool = False

    def rate(self) -> float:
        elapsed = time.time() - self.start_time
        return self.processed / elapsed if elapsed > 0 else 0

    def eta_seconds(self) -> float:
        rate = self.rate()
        remaining = self.total - self.processed
        return remaining / rate if rate > 0 else 0


def build_migrator() -> AsyncZoneMigrator:
    source = PowerDNSConnection(
        base_url=SOURCE_URL,
        api_key=SOURCE_KEY,
        server_id=SOURCE_SERVER_ID,
        verify_ssl=not SOURCE_INSECURE,
    )
    target = PowerDNSConnection(
        base_url=TARGET_URL,
        api_key=TARGET_KEY,
        server_id=TARGET_SERVER_ID,
        verify_ssl=not TARGET_INSECURE,
    )
    return AsyncZoneMigrator(
        source,
        target,
        timeout=TIMEOUT,
        retries=RETRIES,
        retry_backoff=RETRY_BACKOFF,
        retry_max_backoff=RETRY_MAX_BACKOFF,
        retry_jitter=RETRY_JITTER,
        ignore_soa_serial=IGNORE_SOA_SERIAL,
        auto_fix_cname_conflicts=AUTO_FIX_CNAME_CONFLICTS,
        auto_fix_double_cname_conflicts=AUTO_FIX_DOUBLE_CNAME_CONFLICTS,
        normalize_txt_escapes=NORMALIZE_TXT_ESCAPES,
    )


async def get_total_domains(pool: aiomysql.Pool) -> int:
    """Get total count of domains."""
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM domains")
            row = await cur.fetchone()
            return row[0] if row else 0


async def fetch_domains_producer(
    pool: aiomysql.Pool,
    queue: asyncio.Queue[str | None],
    stats: MigrationStats,
) -> None:
    """Fetch domains in batches and put them into the queue."""
    offset = 0

    while not stats.stop_requested:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT name FROM domains ORDER BY id LIMIT %s OFFSET %s",
                    (BATCH_SIZE, offset),
                )
                rows = await cur.fetchall()

                if not rows:
                    break

                for row in rows:
                    if stats.stop_requested:
                        break
                    await queue.put(row[0])

                offset += len(rows)
                logging.debug(
                    "Fetched batch: offset=%d, count=%d", offset - len(rows), len(rows)
                )

    # Signal workers to stop
    for _ in range(CONCURRENCY):
        await queue.put(None)


async def migrate_worker(
    worker_id: int,
    queue: asyncio.Queue[str | None],
    migrator: AsyncZoneMigrator,
    stats: MigrationStats,
) -> None:
    """Worker that pulls zones from queue and migrates them."""
    while True:
        zone = await queue.get()

        if zone is None:
            queue.task_done()
            break

        if stats.stop_requested:
            queue.task_done()
            continue

        # Ensure zone ends with dot
        if not zone.endswith("."):
            zone = f"{zone}."

        try:
            result = await migrator.migrate(zone, recreate=RECREATE, dry_run=DRY_RUN)
            action = result.get("migrator_action", "unknown")
            changes = len(result.get("changes", {}))

            if action == "skipped":
                stats.skipped += 1
                logging.debug("Skipped zone: %s (no changes)", zone)
            else:
                stats.success += 1
                logging.debug(
                    "Migrated zone: %s | action: %s | changes: %d",
                    zone,
                    action,
                    changes,
                )

        except PowerDNSAPIError as exc:
            stats.failed += 1
            logging.error("Zone %s failed: %s", zone, exc)
            if ON_ERROR == "stop":
                stats.stop_requested = True

        finally:
            stats.processed += 1
            queue.task_done()


async def progress_reporter(stats: MigrationStats) -> None:
    """Periodically report migration progress."""
    while not stats.stop_requested and stats.processed < stats.total:
        await asyncio.sleep(PROGRESS_INTERVAL)

        if stats.processed > 0:
            eta = stats.eta_seconds()
            eta_str = (
                f"{int(eta // 3600)}h {int((eta % 3600) // 60)}m {int(eta % 60)}s"
                if eta > 0
                else "calculating..."
            )

            logging.info(
                "Progress: %d/%d (%.1f%%) | success=%d failed=%d skipped=%d | rate=%.1f/s | ETA: %s",
                stats.processed,
                stats.total,
                (stats.processed / stats.total * 100) if stats.total > 0 else 0,
                stats.success,
                stats.failed,
                stats.skipped,
                stats.rate(),
                eta_str,
            )


async def main() -> None:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logging.info(
        "Connecting to MySQL at %s:%d/%s", MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE
    )

    pool = await aiomysql.create_pool(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        db=MYSQL_DATABASE,
        autocommit=True,
        minsize=1,
        maxsize=5,
    )

    try:
        total = await get_total_domains(pool)
        logging.info(
            "Found %d domains to migrate (batch_size=%d, concurrency=%d)",
            total,
            BATCH_SIZE,
            CONCURRENCY,
        )

        if total == 0:
            logging.warning("No domains found in database")
            return

        stats = MigrationStats(total=total)
        migrator = build_migrator()
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=QUEUE_SIZE)

        try:
            # Start producer, workers, and progress reporter
            producer_task = asyncio.create_task(
                fetch_domains_producer(pool, queue, stats)
            )
            worker_tasks = [
                asyncio.create_task(migrate_worker(i, queue, migrator, stats))
                for i in range(CONCURRENCY)
            ]
            progress_task = asyncio.create_task(progress_reporter(stats))

            # Wait for producer to finish
            await producer_task

            # Wait for all items in queue to be processed
            await queue.join()

            # Stop progress reporter
            stats.stop_requested = True
            progress_task.cancel()

            # Wait for workers to finish
            await asyncio.gather(*worker_tasks)

        finally:
            await migrator.close()

        elapsed = time.time() - stats.start_time
        logging.info(
            "Migration complete in %.1fs: total=%d success=%d failed=%d skipped=%d (%.1f zones/s)",
            elapsed,
            stats.total,
            stats.success,
            stats.failed,
            stats.skipped,
            stats.processed / elapsed if elapsed > 0 else 0,
        )

    finally:
        pool.close()
        await pool.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
