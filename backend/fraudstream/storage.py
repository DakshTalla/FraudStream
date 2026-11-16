"""SQLite storage for alerts and analyst feedback.

WHY SQLite: zero setup, a single file, and the standard library. Alerts are
under 1% of traffic, so write volume is tiny. The SQL is plain enough to move
to Postgres by changing the connection line and the placeholder style.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    txn_id      TEXT NOT NULL,
    txn         TEXT NOT NULL,      -- full transaction as JSON
    score       REAL NOT NULL,      -- model probability
    severity    TEXT NOT NULL,      -- high | medium
    reasons     TEXT NOT NULL,      -- SHAP reasons as JSON
    rules       TEXT NOT NULL,      -- fired rules as JSON
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | dismissed
    reviewed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status, id);
"""


class AlertStore:
    def __init__(self, path: Path | str = DB_PATH):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI may call us from a worker thread.
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def add(self, txn: dict, score: float, severity: str, reasons: list, rules: list) -> dict:
        cursor = self.db.execute(
            "INSERT INTO alerts (created_at, txn_id, txn, score, severity, reasons, rules)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), txn["txn_id"], json.dumps(txn), score, severity,
             json.dumps(reasons), json.dumps(rules)))
        self.db.commit()
        return self.get(cursor.lastrowid)

    def get(self, alert_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return self._to_dict(row) if row else None

    def list(self, status: str | None = None, limit: int = 50) -> list[dict]:
        if status:
            rows = self.db.execute(
                "SELECT * FROM alerts WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit))
        else:
            rows = self.db.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
        return [self._to_dict(row) for row in rows]

    def set_status(self, alert_id: int, status: str) -> dict | None:
        """Record the analyst's decision. These labels are what a real system
        would later use to retrain the model (the feedback loop)."""
        self.db.execute("UPDATE alerts SET status = ?, reviewed_at = ? WHERE id = ?",
                        (status, time.time(), alert_id))
        self.db.commit()
        return self.get(alert_id)

    def counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status, COUNT(*) AS n FROM alerts GROUP BY status")
        counts = {"pending": 0, "confirmed": 0, "dismissed": 0}
        counts.update({row["status"]: row["n"] for row in rows})
        return counts

    def clear(self) -> None:
        self.db.execute("DELETE FROM alerts")
        self.db.commit()

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        alert = dict(row)
        for column in ("txn", "reasons", "rules"):
            alert[column] = json.loads(alert[column])
        return alert
