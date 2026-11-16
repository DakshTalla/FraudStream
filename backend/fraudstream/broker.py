"""Message broker abstraction: Kafka/Redpanda when available, in-process otherwise.

The pipeline only talks to the `Broker` interface (publish / subscribe), so
swapping a real distributed log for an asyncio queue changes zero lines of
pipeline code. This is the classic "depend on an interface, not an
implementation" pattern, and it is what lets the tests run without Docker.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
from typing import AsyncIterator, Protocol

from .config import BROKER_KIND, KAFKA_BOOTSTRAP

MAX_BATCH = 256   # upper bound on a micro-batch, keeps worst-case latency bounded


def _to_json(message: dict) -> bytes:
    return json.dumps(message).encode("utf-8")


def _from_json(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


class Broker(Protocol):
    name: str
    async def start(self) -> None: ...
    async def publish(self, topic: str, message: dict) -> None: ...
    def subscribe(self, topic: str) -> AsyncIterator[list[dict]]: ...
    async def stop(self) -> None: ...


class InProcessBroker:
    """One bounded asyncio.Queue per topic."""
    name = "in-process queue"

    def __init__(self, max_size: int = 10_000):
        self._queues: dict[str, asyncio.Queue] = {}
        self._max_size = max_size

    def _queue(self, topic: str) -> asyncio.Queue:
        if topic not in self._queues:
            # WHY bounded: if the consumer falls behind, `put` waits instead
            # of letting memory grow forever. That waiting is backpressure.
            self._queues[topic] = asyncio.Queue(self._max_size)
        return self._queues[topic]

    async def start(self) -> None:
        pass

    async def publish(self, topic: str, message: dict) -> None:
        await self._queue(topic).put(message)

    async def subscribe(self, topic: str) -> AsyncIterator[list[dict]]:
        """Yield micro-batches: wait for one message, then take whatever else
        is ALREADY waiting (never wait to fill a batch). Under light load a
        batch is 1 message, so latency stays minimal; under heavy load batches
        grow by themselves and throughput rises."""
        queue = self._queue(topic)
        while True:
            batch = [await queue.get()]
            while len(batch) < MAX_BATCH and not queue.empty():
                batch.append(queue.get_nowait())
            yield batch

    async def stop(self) -> None:
        pass


class KafkaBroker:
    """Thin wrapper over kafka-python. Works with Kafka or Redpanda
    (Redpanda speaks the Kafka protocol, but is a single light container)."""
    name = "Redpanda / Kafka"

    def __init__(self, bootstrap: str = KAFKA_BOOTSTRAP, group_id: str = "fraud-scorer"):
        self._bootstrap = bootstrap
        self._group_id = group_id
        self._producer = None
        self._stopping = threading.Event()

    async def start(self) -> None:
        from kafka import KafkaProducer
        self._producer = KafkaProducer(
            bootstrap_servers=self._bootstrap,
            value_serializer=_to_json,
            linger_ms=5,   # wait up to 5 ms to batch messages: more throughput
            acks=1,        # leader acknowledgement: good balance of speed and safety
        )

    async def publish(self, topic: str, message: dict) -> None:
        # send() only appends to an in-memory batch; a background thread does
        # the network I/O, so this does not block the event loop.
        # Keying by payer sends all of one account's events to the same
        # partition, which preserves their order (needed for velocity features).
        self._producer.send(topic, message, key=str(message.get("payer", "")).encode("utf-8"))

    async def subscribe(self, topic: str) -> AsyncIterator[list[dict]]:
        from kafka import KafkaConsumer
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(8)   # holds batches, not messages
        consumer = KafkaConsumer(
            topic, bootstrap_servers=self._bootstrap, group_id=self._group_id,
            value_deserializer=_from_json,
            auto_offset_reset="latest",   # a fresh consumer starts from "now"
        )

        def poll_forever() -> None:
            # kafka-python is blocking, so it lives in its own thread and
            # hands messages to the asyncio world through a bounded queue.
            # One poll() already returns a batch, which we pass on as-is.
            while not self._stopping.is_set():
                polled = consumer.poll(timeout_ms=200, max_records=MAX_BATCH)
                batch = [record.value for records in polled.values() for record in records]
                if batch:
                    future = asyncio.run_coroutine_threadsafe(queue.put(batch), loop)
                    future.result()   # blocks this thread if the scorer is behind
            consumer.close()

        threading.Thread(target=poll_forever, daemon=True, name="kafka-consumer").start()
        while True:
            yield await queue.get()

    async def stop(self) -> None:
        self._stopping.set()
        if self._producer is not None:
            self._producer.flush(timeout=2)
            self._producer.close(timeout=2)


def kafka_reachable(bootstrap: str = KAFKA_BOOTSTRAP, timeout: float = 0.5) -> bool:
    host, _, port = bootstrap.partition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def make_broker(kind: str = BROKER_KIND) -> Broker:
    """auto: use Kafka/Redpanda if something is listening, else in-process."""
    if kind == "kafka" or (kind == "auto" and kafka_reachable()):
        return KafkaBroker()
    return InProcessBroker()
