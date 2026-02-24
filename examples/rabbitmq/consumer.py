#!/usr/bin/env python3
# Consume zones from RabbitMQ and migrate them
# pip install powerdns-migrator aio-pika

import asyncio
import json
import os
import logging
from typing import Optional

import aio_pika

from powerdns_migrator.async_migrator import AsyncZoneMigrator
from powerdns_migrator.config import PowerDNSConnection
from powerdns_migrator.errors import PowerDNSMigratorError

# RabbitMQ configuration
RABBIT_URL = os.getenv("RABBIT_URL", "amqp://admin:pass2login@localhost/")
QUEUE_NAME = os.getenv("QUEUE_NAME", "powerdns-migrator")

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


async def handle_message(
    message: aio_pika.abc.AbstractIncomingMessage, migrator: AsyncZoneMigrator
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
            result = await migrator.migrate(zone, recreate=RECREATE, dry_run=DRY_RUN)
            logging.info(
                "Migrated zone: %s | action: %s | changes: %d",
                zone,
                result.get("migrator_action"),
                len(result.get("changes", {})),
            )
        except PowerDNSMigratorError as exc:
            logging.error("Zone %s failed: %s", zone, exc)
            raise  # requeue


# aio_pika.connect_robust - Auto-reconnecting connection
# Automatically reconnects if the connection is lost
# Redeclares queues, exchanges, and bindings after reconnection
# Has internal heartbeat monitoring
# Best for: long-running consumers, services that need to stay connected


async def main() -> None:
    logging.basicConfig(level=LOG_LEVEL, format="[%(levelname)s] %(message)s")

    connection = await aio_pika.connect_robust(RABBIT_URL)
    async with connection:
        channel = await connection.channel()
        async with channel:
            await channel.set_qos(prefetch_count=1)

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
