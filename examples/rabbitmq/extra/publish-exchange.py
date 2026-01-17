#!/usr/bin/env python3
# Publish zones to RabbitMQ exchange (for fan-out or routing scenarios)
# pip install aio-pika

import asyncio
import os
import logging

import aio_pika

RABBIT_URL = os.getenv("RABBIT_URL", "amqp://admin:pass2login@localhost/")
EXCHANGE_NAME = os.getenv("EXCHANGE_NAME", "powerdns-migrator")
QUEUE_NAME = os.getenv("QUEUE_NAME", "powerdns-migrator")
ROUTING_KEY = os.getenv("ROUTING_KEY", "zones")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

TEST_ZONES = [
    "zone1.test.",
    "zone2.test.",
    "zone3.test.",
]


async def main() -> None:
    logging.basicConfig(level=LOG_LEVEL, format="[%(levelname)s] %(message)s")

    connection = await aio_pika.connect(RABBIT_URL)
    channel = await connection.channel()

    # Declare a direct exchange (can also use FANOUT, TOPIC, HEADERS)
    exchange = await channel.declare_exchange(
        EXCHANGE_NAME,
        aio_pika.ExchangeType.DIRECT,
        durable=True,
    )

    # Declare queue and bind it to the exchange
    queue = await channel.declare_queue(QUEUE_NAME, durable=True)
    await queue.bind(exchange, routing_key=ROUTING_KEY)
    logging.info(
        "Queue '%s' bound to exchange '%s' with routing_key '%s'",
        QUEUE_NAME,
        EXCHANGE_NAME,
        ROUTING_KEY,
    )

    for zone in TEST_ZONES:
        message = aio_pika.Message(
            body=zone.encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await exchange.publish(message, routing_key=ROUTING_KEY)
        logging.info("Published zone: %s (routing_key: %s)", zone, ROUTING_KEY)

    logging.info("Published %d zones to exchange '%s'", len(TEST_ZONES), EXCHANGE_NAME)

    await channel.close()
    await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
