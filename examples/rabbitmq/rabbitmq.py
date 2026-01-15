#!/usr/bin/env python3
# Install deps:
# pip install powerdns-migrator aio-pika
# Set env vars (example):
# export RABBIT_URL=amqp://guest:guest@localhost/export
# QUEUE_NAME=pdns-zonesexport
# SOURCE_URL=http://pdns-source:8081export
# SOURCE_KEY=...export
# TARGET_URL=http://pdns-target:8081export
# TARGET_KEY=...
# If you want concurrency per message (e.g., process multiple messages in parallel), I can add a worker pool with a semaphore.

import asyncio
import json
import os
import logging
from typing import Optional

import aio_pika

from powerdns_migrator.async_migrator import AsyncZoneMigrator
from powerdns_migrator.config import PowerDNSConnection
from powerdns_migrator.errors import PowerDNSAPIError, MigrationError

RABBIT_URL = os.getenv("RABBIT_URL", "amqp://guest:guest@localhost/")
QUEUE_NAME = os.getenv("QUEUE_NAME", "pdns-zones")

SOURCE_URL = os.getenv("SOURCE_URL", "http://pdns-source:8081")
SOURCE_KEY = os.getenv("SOURCE_KEY", "")
SOURCE_SERVER_ID = os.getenv("SOURCE_SERVER_ID", "localhost")

TARGET_URL = os.getenv("TARGET_URL", "http://pdns-target:8081")
TARGET_KEY = os.getenv("TARGET_KEY", "")
TARGET_SERVER_ID = os.getenv("TARGET_SERVER_ID", "localhost")

RECREATE = os.getenv("RECREATE", "false").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

TIMEOUT = float(os.getenv("TIMEOUT", "10"))
RETRIES = int(os.getenv("RETRIES", "3"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "0.5"))
RETRY_MAX_BACKOFF = float(os.getenv("RETRY_MAX_BACKOFF", "5.0"))
RETRY_JITTER = float(os.getenv("RETRY_JITTER", "0.1"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def build_migrator() -> AsyncZoneMigrator:
    source = PowerDNSConnection(
        base_url=SOURCE_URL,
        api_key=SOURCE_KEY,
        server_id=SOURCE_SERVER_ID,
        verify_ssl=True,
    )
    target = PowerDNSConnection(
        base_url=TARGET_URL,
        api_key=TARGET_KEY,
        server_id=TARGET_SERVER_ID,
        verify_ssl=True,
    )
    return AsyncZoneMigrator(
        source,
        target,
        timeout=TIMEOUT,
        retries=RETRIES,
        retry_backoff=RETRY_BACKOFF,
        retry_max_backoff=RETRY_MAX_BACKOFF,
        retry_jitter=RETRY_JITTER,
    )


async def handle_message(
    message: aio_pika.IncomingMessage, migrator: AsyncZoneMigrator
) -> None:
    async with message.process(requeue=True):
        body = message.body.decode("utf-8").strip()
        zone: Optional[str] = None

        # Message can be JSON like {"zone": "example.com."} or raw string "example.com."
        try:
            if body.startswith("{"):
                payload = json.loads(body)
                zone = payload.get("zone")
            else:
                zone = body
        except json.JSONDecodeError:
            zone = body

        if not zone:
            logging.warning("Message without zone: %s", body)
            return

        logging.info("Migrating zone: %s", zone)
        try:
            await migrator.migrate(zone, recreate=RECREATE, dry_run=DRY_RUN)
            logging.info("Migrated zone: %s", zone)
        except (PowerDNSAPIError, MigrationError) as exc:
            logging.error("Zone %s failed: %s", zone, exc)
            raise  # requeue


async def main() -> None:
    logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s %(message)s")

    connection = await aio_pika.connect_robust(RABBIT_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=50)

        queue = await channel.declare_queue(QUEUE_NAME, durable=True)

        migrator = build_migrator()
        try:
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    await handle_message(message, migrator)
        finally:
            await migrator.close()


if __name__ == "__main__":
    asyncio.run(main())
