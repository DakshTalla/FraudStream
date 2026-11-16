"""FastAPI app: wires simulator -> broker -> pipeline -> WebSocket dashboard.

    uvicorn fraudstream.api:app --port 8000

Two background tasks run inside the server process:
  producer: simulator -> broker topic "transactions"
  scorer:   broker topic -> Pipeline.process -> WebSocket broadcast
They only communicate through the broker, exactly as two separate services
would, so they could be split into separate processes without code changes.
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .broker import make_broker
from .config import LIVE_TPS, TOPIC_TRANSACTIONS
from .model import FraudModel
from .pipeline import Pipeline
from .schema import Transaction
from .simulator import Simulator
from .storage import AlertStore

WARM_UP_TXNS = 150_000
TICKER_MAX_PER_SEC = 30   # the UI cannot usefully show more rows than this


class Hub:
    """Keeps track of connected dashboards and broadcasts JSON to all of them."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def broadcast(self, message: dict) -> None:
        for client in list(self.clients):
            try:
                await client.send_json(message)
            except Exception:
                # A closed or broken browser tab must not stall the pipeline.
                self.clients.discard(client)


hub = Hub()
state: dict = {}


async def produce(simulator: Simulator, broker, tps: float) -> None:
    """Publish simulated transactions at a steady rate."""
    interval = 1.0 / tps
    next_at = time.perf_counter()
    for txn in simulator.stream():
        txn.emitted_at = time.time()
        await broker.publish(TOPIC_TRANSACTIONS, txn.to_dict())
        # Schedule against absolute time so small sleep errors do not add up.
        next_at += interval
        await asyncio.sleep(max(0.0, next_at - time.perf_counter()))


async def score(pipeline: Pipeline, broker, tps: float) -> None:
    """Consume transactions, score them, push results to dashboards."""
    sample_every = max(1, math.ceil(tps / TICKER_MAX_PER_SEC))
    async for message in broker.subscribe(TOPIC_TRANSACTIONS):
        event = pipeline.process(Transaction.from_dict(message))
        if event["is_alert"]:
            await hub.broadcast({"type": "alert", "alert": event["alert"]})
        if event["is_alert"] or pipeline.processed % sample_every == 0:
            await hub.broadcast({"type": "txn", "txn": event["txn"],
                                 "score": event["score"], "is_alert": event["is_alert"]})


async def publish_stats(pipeline: Pipeline) -> None:
    while True:
        await asyncio.sleep(1.0)
        await hub.broadcast({"type": "stats", "stats": pipeline.stats()})


@asynccontextmanager
async def lifespan(app: FastAPI):
    model = FraudModel()
    store = AlertStore()
    store.clear()   # each demo run starts with an empty queue
    pipeline = Pipeline(model, store)
    # WHY a different seed from training (42): the live stream is data the
    # model has never seen, so the live precision/recall is an honest check.
    simulator = Simulator(seed=7)
    pipeline.warm_up(simulator, WARM_UP_TXNS)
    broker = make_broker()
    await broker.start()
    state.update(pipeline=pipeline, store=store, model=model, broker=broker)
    tasks = [asyncio.create_task(score(pipeline, broker, LIVE_TPS)),
             asyncio.create_task(produce(simulator, broker, LIVE_TPS)),
             asyncio.create_task(publish_stats(pipeline))]
    yield
    for task in tasks:
        task.cancel()
    with contextlib.suppress(Exception):
        await broker.stop()


app = FastAPI(title="FraudStream", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


class Feedback(BaseModel):
    decision: Literal["confirmed", "dismissed"]


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "broker": state["broker"].name, "tps_target": LIVE_TPS}


@app.get("/api/stats")
def stats() -> dict:
    return state["pipeline"].stats()


@app.get("/api/alerts")
def alerts(status: str | None = None, limit: int = 50) -> list[dict]:
    return state["store"].list(status, min(limit, 200))


@app.post("/api/alerts/{alert_id}/feedback")
async def feedback(alert_id: int, body: Feedback) -> dict:
    alert = state["store"].set_status(alert_id, body.decision)
    if alert is None:
        raise HTTPException(404, "alert not found")
    await hub.broadcast({"type": "alert_update", "alert": alert})
    return alert


@app.get("/api/metrics")
def metrics() -> dict:
    """Offline evaluation written by train.py (PR curve, precision, recall)."""
    return state["model"].metrics


@app.get("/api/graph")
def graph() -> list[dict]:
    return state["pipeline"].rings.rings()


@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await ws.accept()
    hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()   # we only push; this just detects disconnects
    except WebSocketDisconnect:
        hub.clients.discard(ws)
