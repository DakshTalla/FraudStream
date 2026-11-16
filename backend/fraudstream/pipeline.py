"""The scoring pipeline: one transaction in, one decision out.

    features -> rules + model -> (if suspicious) SHAP reasons -> store alert

This class has no knowledge of Kafka, HTTP or WebSockets, so the API, the
benchmark and the tests can all reuse it.
"""
from __future__ import annotations

import time
from collections import deque

import numpy as np

from . import rules
from .features import FeatureStore
from .graph import RingTracker
from .model import FraudModel
from .schema import Transaction
from .simulator import Simulator
from .storage import AlertStore


class Pipeline:
    def __init__(self, model: FraudModel, store: AlertStore):
        self.model = model
        self.store = store
        self.features = FeatureStore()
        self.rings = RingTracker()
        self.processed = 0
        self.started_at = time.time()
        # Live confusion matrix. Possible only because the simulator tells us
        # the truth; a real bank gets labels days later.
        self.confusion = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        self._latencies_ms: deque[float] = deque(maxlen=5000)
        self._recent_times: deque[float] = deque(maxlen=20000)

    def warm_up(self, simulator: Simulator, n_txns: int) -> None:
        """Replay history to build account state before scoring starts.

        WHY: with empty state every device and city looks "new" and the model
        would alert on everything. In production you would rebuild this state
        by replaying the Kafka topic from an earlier offset.
        """
        for txn in simulator.take(n_txns):
            self.features.update(txn)

    def process(self, txn: Transaction) -> dict:
        """Score one transaction. Returns the event sent to the dashboard."""
        features = self.features.compute_and_update(txn)
        score = self.model.score(features)
        fired = rules.evaluate(features)
        model_flag = score >= self.model.threshold
        is_alert = model_flag or bool(fired)

        alert = None
        if is_alert:
            # WHY explain only alerts: SHAP is the slowest step, and under 1%
            # of traffic needs an explanation. Keeps median latency low.
            reasons = self.model.explain(features)
            severity = "high" if (model_flag and fired) or score >= 0.9 else "medium"
            alert = self.store.add(
                txn.to_dict(), round(score, 4), severity, reasons,
                [{"name": r.name, "description": r.description} for r in fired])
            self.rings.add(txn.payer, txn.payee, txn.amount)

        key = ("tp" if txn.is_fraud else "fp") if is_alert else ("fn" if txn.is_fraud else "tn")
        self.confusion[key] += 1
        self.processed += 1
        now = time.time()
        self._recent_times.append(now)
        if txn.emitted_at:
            self._latencies_ms.append((now - txn.emitted_at) * 1000)
        return {"txn": txn.to_dict(), "score": round(score, 4), "is_alert": is_alert,
                "alert": alert}

    def stats(self) -> dict:
        now = time.time()
        last_5s = sum(1 for t in reversed(self._recent_times) if now - t <= 5.0)
        c = self.confusion
        latency = {}
        if self._latencies_ms:
            p50, p95, p99 = np.percentile(self._latencies_ms, [50, 95, 99])
            latency = {"p50": round(float(p50), 2), "p95": round(float(p95), 2),
                       "p99": round(float(p99), 2)}
        return {
            "processed": self.processed,
            "throughput_tps": round(last_5s / 5.0, 1),
            "latency_ms": latency,
            "alerts": c["tp"] + c["fp"],
            "confusion": dict(c),
            "live_precision": round(c["tp"] / max(c["tp"] + c["fp"], 1), 4),
            "live_recall": round(c["tp"] / max(c["tp"] + c["fn"], 1), 4),
            "review": self.store.counts(),
            "uptime_s": round(now - self.started_at),
        }
