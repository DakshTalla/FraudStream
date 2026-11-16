"""End-to-end checks using a small model trained in the session fixture."""
import time

import numpy as np
import pytest
import xgboost as xgb
from fastapi.testclient import TestClient

from fraudstream import api
from fraudstream.broker import InProcessBroker
from fraudstream.config import FEATURE_NAMES
from fraudstream.features import to_vector
from fraudstream.pipeline import Pipeline
from fraudstream.simulator import Simulator
from fraudstream.storage import AlertStore
from fraudstream.train import pick_threshold

from .conftest import make_txn


def test_training_beats_random_by_a_wide_margin(trained):
    m = trained.metrics
    # A random classifier's PR-AUC equals the fraud rate (about 0.01).
    assert m["pr_auc"] > 0.7
    assert m["model"]["precision"] > 0.6 and m["model"]["recall"] > 0.6
    assert m["feature_names"] == FEATURE_NAMES
    assert 0 < m["threshold"] < 1


def test_pick_threshold_meets_precision_target():
    y = np.array([0] * 90 + [1] * 10)
    scores = np.concatenate([np.linspace(0.0, 0.5, 90), np.linspace(0.6, 1.0, 10)])
    threshold = pick_threshold(y, scores)
    flagged = scores >= threshold
    precision = (flagged & (y == 1)).sum() / flagged.sum()
    assert precision >= 0.9            # the target is met ...
    assert flagged[y == 1].all()       # ... without giving up any recall


def test_shap_contributions_add_up_to_the_model_output(trained):
    """SHAP's defining property: base value + contributions = raw log-odds."""
    pipeline = Pipeline(trained, AlertStore(":memory:"))
    features = pipeline.features.compute_and_update(make_txn(amount=90_000))
    row = np.asarray([to_vector(features)], dtype=np.float32)
    values = trained.shap_values(features)      # last entry is the base value
    margin = trained.booster.predict(xgb.DMatrix(row), output_margin=True)[0]
    assert values.sum() == pytest.approx(margin, abs=1e-3)
    assert len(values) == len(FEATURE_NAMES) + 1


def test_pipeline_alerts_on_account_takeover_but_not_on_routine_payment(trained):
    pipeline = Pipeline(trained, AlertStore(":memory:"))
    t0 = 1_767_256_200.0   # 2 PM IST on the first simulated day
    for i in range(30):    # build a history of small local payments
        pipeline.features.update(make_txn(ts=t0 + i * 3600, amount=250, payee="shop001@upi",
                                          payee_type="merchant"))
    routine = pipeline.process(make_txn(ts=t0 + 31 * 3600, amount=260, payee="shop001@upi",
                                        payee_type="merchant"))
    assert not routine["is_alert"]

    takeover = pipeline.process(make_txn(ts=t0 + 31 * 3600 + 300, amount=60_000,
                                         device="dev-attacker", city="Kolkata",
                                         payee="stranger@upi", is_fraud=True))
    assert takeover["is_alert"]
    alert = takeover["alert"]
    assert alert["reasons"], "every alert must carry at least one SHAP reason"
    assert all(r["contribution"] > 0 for r in alert["reasons"])
    assert "impossible_travel" in [r["name"] for r in alert["rules"]]
    assert pipeline.confusion == {"tp": 1, "fp": 0, "fn": 0, "tn": 1}


def test_live_stream_quality_on_unseen_seed(trained):
    pipeline = Pipeline(trained, AlertStore(":memory:"))
    simulator = Simulator(seed=99)
    # Long warm-up on purpose: with little history every account looks "young"
    # and the mule rule over-fires (the cold-start problem, see STUDY_GUIDE).
    pipeline.warm_up(simulator, 100_000)
    for txn in simulator.take(15_000):
        pipeline.process(txn)
    stats = pipeline.stats()
    assert stats["processed"] == 15_000
    assert stats["live_precision"] > 0.6 and stats["live_recall"] > 0.6


def test_api_serves_alerts_and_accepts_feedback(trained, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "FraudModel", lambda: trained)
    monkeypatch.setattr(api, "AlertStore", lambda: AlertStore(tmp_path / "alerts.db"))
    monkeypatch.setattr(api, "make_broker", InProcessBroker)
    monkeypatch.setattr(api, "WARM_UP_TXNS", 5_000)
    monkeypatch.setattr(api, "LIVE_TPS", 2000.0)

    with TestClient(api.app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert "pr_curve" in client.get("/api/metrics").json()

        with client.websocket_connect("/ws") as ws:
            kinds = {ws.receive_json()["type"] for _ in range(20)}
        assert "txn" in kinds

        # Wait (bounded) for the stream to raise at least one alert.
        deadline = time.time() + 20
        alerts = []
        while not alerts and time.time() < deadline:
            time.sleep(0.2)
            alerts = client.get("/api/alerts").json()
        assert alerts, "no alert raised within 20 seconds"

        alert_id = alerts[0]["id"]
        response = client.post(f"/api/alerts/{alert_id}/feedback",
                               json={"decision": "confirmed"})
        assert response.json()["status"] == "confirmed"
        assert client.post("/api/alerts/999999/feedback",
                           json={"decision": "dismissed"}).status_code == 404
        assert client.post(f"/api/alerts/{alert_id}/feedback",
                           json={"decision": "maybe"}).status_code == 422
        assert isinstance(client.get("/api/graph").json(), list)
        assert client.get("/api/stats").json()["processed"] > 0
