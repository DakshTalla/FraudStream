"""Measure throughput and latency. Numbers in the README come from here.

    python -m fraudstream.benchmark            # in-process queue
    python -m fraudstream.benchmark --broker kafka

Two measurements:
1. max throughput: push N transactions through the broker as fast as possible
   and time how long the scorer takes to finish them all.
2. latency at a steady rate: publish at a fixed txns/sec and record, for every
   transaction, (time scored - time published). This is end-to-end latency:
   serialise + broker + features + model + rules + (for alerts) SHAP + SQLite.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import time
from pathlib import Path

import numpy as np

from .broker import make_broker
from .config import BACKEND_DIR
from .model import FraudModel
from .pipeline import Pipeline
from .schema import Transaction
from .simulator import Simulator
from .storage import AlertStore

OUT_PATH = BACKEND_DIR.parent / "docs" / "benchmark.json"


async def run(broker_kind: str, n_max: int, rate: float, seconds: float) -> dict:
    pipeline = Pipeline(FraudModel(), AlertStore(":memory:"))
    simulator = Simulator(seed=11)
    pipeline.warm_up(simulator, 340_000)   # same maturity as the API
    broker = make_broker(broker_kind)
    await broker.start()
    topic = f"bench-{int(time.time())}"   # fresh topic so old runs do not mix in
    stream = broker.subscribe(topic)

    latencies_ms: list[float] = []
    batch_sizes: list[int] = []

    async def consume(count: int) -> None:
        done = 0
        while done < count:
            batch = await anext(stream)
            pipeline.process_batch([Transaction.from_dict(m) for m in batch])
            finished = time.time()
            latencies_ms.extend((finished - m["emitted_at"]) * 1000 for m in batch)
            batch_sizes.append(len(batch))
            done += len(batch)

    async def publish(txn: Transaction) -> None:
        txn.emitted_at = time.time()
        await broker.publish(topic, txn.to_dict())

    # Kafka consumers need a moment to join the group before messages count.
    consumer = asyncio.create_task(consume(1))
    while not consumer.done():
        await publish(next(simulator.stream()))
        await asyncio.sleep(0.2)

    # --- 1. max throughput ---
    txns = simulator.take(n_max)
    consumer = asyncio.create_task(consume(n_max))
    started = time.perf_counter()
    for i, txn in enumerate(txns):
        await publish(txn)
        if i % 50 == 0:
            await asyncio.sleep(0)   # give the consumer task a turn
    await consumer
    throughput = n_max / (time.perf_counter() - started)

    # --- 2. latency at a steady rate ---
    mean_batch_max = float(np.mean(batch_sizes))
    latencies_ms.clear()
    batch_sizes.clear()
    n_paced = int(rate * seconds)
    consumer = asyncio.create_task(consume(n_paced))
    next_at = time.perf_counter()
    for txn in simulator.take(n_paced):
        await publish(txn)
        next_at += 1.0 / rate
        await asyncio.sleep(max(0.0, next_at - time.perf_counter()))
    await consumer
    await broker.stop()

    p50, p95, p99 = np.percentile(latencies_ms, [50, 95, 99])
    return {
        "broker": broker.name,
        "machine": f"{platform.processor()} / Python {platform.python_version()}",
        "max_throughput_tps": round(throughput),
        "max_throughput_txns": n_max,
        "mean_batch_at_max": round(mean_batch_max, 1),
        "paced_rate_tps": rate,
        "paced_txns": n_paced,
        "mean_batch_at_paced": round(float(np.mean(batch_sizes)), 2),
        "latency_ms": {"p50": round(float(p50), 2), "p95": round(float(p95), 2),
                       "p99": round(float(p99), 2)},
        "alerts_raised": pipeline.confusion["tp"] + pipeline.confusion["fp"],
        "live_precision": pipeline.stats()["live_precision"],
        "live_recall": pipeline.stats()["live_recall"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FraudStream benchmark")
    parser.add_argument("--broker", default="memory", choices=["memory", "kafka"])
    parser.add_argument("--n", type=int, default=50_000, help="txns for max throughput")
    parser.add_argument("--rate", type=float, default=500, help="txns/sec for latency test")
    parser.add_argument("--seconds", type=float, default=20)
    args = parser.parse_args()

    result = asyncio.run(run(args.broker, args.n, args.rate, args.seconds))
    print(json.dumps(result, indent=2))
    OUT_PATH.parent.mkdir(exist_ok=True)
    existing = json.loads(OUT_PATH.read_text(encoding="utf-8")) if OUT_PATH.exists() else {}
    existing[args.broker] = result
    OUT_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
