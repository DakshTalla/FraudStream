"""Shared fixtures. A small model is trained once per test session."""
import json

import pytest

from fraudstream import train as train_module
from fraudstream.model import FraudModel
from fraudstream.schema import Transaction


def make_txn(ts=1_000_000.0, payer="alice@upi", payee="bob@upi", amount=500.0,
             device="dev-a", city="Mumbai", payee_type="person", **extra) -> Transaction:
    """Build a transaction with sensible defaults so tests state only what matters."""
    return Transaction(txn_id=f"T{int(ts)}", ts=ts, payer=payer, payee=payee,
                       payee_type=payee_type, amount=amount, device_id=device,
                       city=city, **extra)


@pytest.fixture(scope="session")
def trained(tmp_path_factory, monkeypatch_session) -> FraudModel:
    """Train on a small history into a temp folder (never touches models/)."""
    folder = tmp_path_factory.mktemp("model")
    monkeypatch_session.setattr(train_module, "MODEL_DIR", folder)
    monkeypatch_session.setattr(train_module, "MODEL_PATH", folder / "model.json")
    monkeypatch_session.setattr(train_module, "METRICS_PATH", folder / "metrics.json")
    train_module.train(n_txns=60_000, seed=1)
    json.loads((folder / "metrics.json").read_text(encoding="utf-8"))  # valid JSON
    return FraudModel(folder / "model.json", folder / "metrics.json")


@pytest.fixture(scope="session")
def monkeypatch_session():
    patcher = pytest.MonkeyPatch()
    yield patcher
    patcher.undo()
