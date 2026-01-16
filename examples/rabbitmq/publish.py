#!/usr/bin/env python3
# Publish test zones to RabbitMQ queue
# pip install aio-pika

import asyncio
import os
import logging

import aio_pika

RABBIT_URL = os.getenv("RABBIT_URL", "amqp://admin:pass2login@localhost/")
QUEUE_NAME = os.getenv("QUEUE_NAME", "powerdns-migrator")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

TEST_ZONES = [
    "zone1.test.",
    "zone2.test.",
    "zone3.test.",
]


# aio_pika.connect - Simple connection
# Opens a single connection to RabbitMQ
# If the connection drops, it stays disconnected
# Best for: one-shot scripts, short-lived publishers
async def main() -> None:
    logging.basicConfig(level=LOG_LEVEL, format="[%(levelname)s] %(message)s")

    connection = await aio_pika.connect(RABBIT_URL)
    channel = await connection.channel()
    await channel.declare_queue(QUEUE_NAME, durable=True)

    for zone in TEST_ZONES:
        message = aio_pika.Message(
            body=zone.encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await channel.default_exchange.publish(
            message,
            routing_key=QUEUE_NAME,
        )
        logging.info("Published zone: %s", zone)

    logging.info("Published %d test zones", len(TEST_ZONES))

    await channel.close()
    await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
