"""Loads the trained XGBoost model, scores one transaction, explains the score."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import xgboost as xgb

from .config import FEATURE_NAMES, METRICS_PATH, MODEL_PATH
from .features import to_vector

# Plain-English templates for the analyst. {v} is the feature's value.
REASON_TEXT = {
    "amount": "Payment amount is Rs {v:,.0f}",
    "hour": "Made at {v:.0f}:00 IST",
    "is_night": "Made late at night (before 5 AM)",
    "is_p2m": "Payee type (merchant vs person) is unusual for this pattern",
    "payer_txn_count": "Payer has only {v:.0f} earlier payments on record",
    "cnt_10m": "{v:.0f} payments by this payer in the last 10 minutes",
    "cnt_1h": "{v:.0f} payments by this payer in the last hour",
    "cnt_24h": "{v:.0f} payments by this payer in the last 24 hours",
    "sum_1h": "Payer sent Rs {v:,.0f} in the last hour",
    "distinct_payees_1h": "Payer paid {v:.0f} different payees in the last hour",
    "amount_z": "Amount is {v:+.1f} standard deviations from the payer's usual",
    "new_device": "Device never seen before for this payer",
    "new_city": "City never seen before for this payer",
    "speed_kmh": "Implied travel speed since last payment: {v:,.0f} km/h",
    "secs_since_last": "{v:,.0f} seconds since the payer's previous payment",
    "new_payee": "First ever payment to this payee",
    "payee_in_degree_24h": "Payee received from {v:.0f} different payers in 24h",
    "payee_inflow_24h": "Payee received Rs {v:,.0f} in the last 24h",
    "payee_txn_count": "Payee account has only {v:.0f} earlier incoming payments",
    "payee_passthrough_24h": "Payee already forwarded {v:.0%} of the money it received",
    "payer_secs_since_received": "Payer received money just {v:,.0f} seconds ago",
    "payer_in_degree_24h": "Payer itself received from {v:.0f} different accounts in 24h",
    "fwd_ratio": "This payment forwards {v:.0%} of what the payer received in 24h",
}


class FraudModel:
    def __init__(self, model_path: Path = MODEL_PATH, metrics_path: Path = METRICS_PATH):
        if not model_path.exists():
            raise FileNotFoundError(
                f"{model_path} not found. Train first: python -m fraudstream.train")
        self.booster = xgb.Booster()
        self.booster.load_model(str(model_path))
        self.metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        self.threshold: float = self.metrics["threshold"]

    def score_batch(self, feature_rows: list[dict[str, float]]) -> np.ndarray:
        """Fraud probability in [0, 1] for each transaction, in ONE model call.

        WHY batch: a single XGBoost call has a fixed overhead (~0.5 ms of
        Python/C bookkeeping) that dwarfs the tree walk itself. Scoring 100
        rows costs almost the same as scoring 1, so batching what is already
        waiting multiplies throughput without adding latency.
        """
        rows = np.asarray([to_vector(f) for f in feature_rows], dtype=np.float32)
        # inplace_predict skips building a DMatrix, saving another copy.
        return self.booster.inplace_predict(rows)

    def score(self, features: dict[str, float]) -> float:
        return float(self.score_batch([features])[0])

    def shap_values(self, features: dict[str, float]) -> np.ndarray:
        """Exact TreeSHAP values: one per feature, plus the base value last.

        WHY XGBoost's built-in `pred_contribs` instead of the `shap` package:
        it is the same TreeSHAP algorithm (the shap authors contributed it to
        XGBoost), it needs no extra dependency, and the additivity test in
        tests/ passes with it. shap 0.54 mis-read the base value of an
        XGBoost 3.x model (reported 0), which broke that guarantee.
        """
        row = xgb.DMatrix(np.asarray([to_vector(features)], dtype=np.float32))
        return self.booster.predict(row, pred_contribs=True)[0]

    def explain(self, features: dict[str, float], top_k: int = 4) -> list[dict]:
        """Top features that pushed THIS score towards fraud.

        SHAP splits the model output (in log-odds) into one additive
        contribution per feature: base_value + sum(contributions) = log-odds.
        We only show positive contributions because an analyst wants to know
        "why was this flagged", not "what made it look a bit safer".
        """
        contributions = self.shap_values(features)[:-1]
        order = np.argsort(contributions)[::-1][:top_k]
        reasons = []
        for i in order:
            if contributions[i] <= 0:
                break
            name = FEATURE_NAMES[i]
            reasons.append({
                "feature": name,
                "value": round(float(features[name]), 3),
                "contribution": round(float(contributions[i]), 3),
                "text": REASON_TEXT[name].format(v=features[name]),
            })
        return reasons
