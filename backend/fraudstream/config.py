"""Shared constants. Everything tunable lives here so no file has magic numbers."""
from __future__ import annotations

import math
import os
from pathlib import Path

# ---- paths -----------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = BACKEND_DIR / "models"
DATA_DIR = BACKEND_DIR / "data"
MODEL_PATH = MODEL_DIR / "model.json"
METRICS_PATH = MODEL_DIR / "metrics.json"
DB_PATH = DATA_DIR / "alerts.db"

# ---- runtime settings (overridable through environment variables) ----------
BROKER_KIND = os.getenv("FRAUDSTREAM_BROKER", "auto")  # auto | kafka | memory
KAFKA_BOOTSTRAP = os.getenv("FRAUDSTREAM_KAFKA", "localhost:19092")
LIVE_TPS = float(os.getenv("FRAUDSTREAM_TPS", "15"))  # simulated txns per second
TOPIC_TRANSACTIONS = "transactions"

# ---- time ------------------------------------------------------------------
MINUTE, HOUR, DAY = 60.0, 3600.0, 86400.0
WEEK = 7 * DAY
# 2026-01-01 00:00 IST as a unix timestamp. The simulator's clock starts here.
START_TS = 1767205800.0
IST_OFFSET = 5.5 * HOUR


def ist_hour(ts: float) -> int:
    """Hour of day (0-23) in Indian Standard Time for a unix timestamp."""
    return int(((ts + IST_OFFSET) % DAY) // HOUR)


# ---- geography -------------------------------------------------------------
CITIES: dict[str, tuple[float, float]] = {
    "Mumbai": (19.08, 72.88),
    "Delhi": (28.61, 77.21),
    "Bengaluru": (12.97, 77.59),
    "Hyderabad": (17.39, 78.49),
    "Chennai": (13.08, 80.27),
    "Kolkata": (22.57, 88.36),
    "Pune": (18.52, 73.86),
    "Ahmedabad": (23.02, 72.57),
    "Jaipur": (26.91, 75.79),
    "Lucknow": (26.85, 80.95),
}


def haversine_km(city_a: str, city_b: str) -> float:
    """Great-circle distance between two known cities, in kilometres."""
    lat1, lon1 = map(math.radians, CITIES[city_a])
    lat2, lon2 = map(math.radians, CITIES[city_b])
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


# ---- model features --------------------------------------------------------
# WHY a single list: the training script and the live scorer both build their
# input vector from this exact order, so the columns can never get out of sync.
FEATURE_NAMES: list[str] = [
    "amount",
    "hour",
    "is_night",
    "is_p2m",
    "payer_txn_count",
    "cnt_10m",
    "cnt_1h",
    "cnt_24h",
    "sum_1h",
    "distinct_payees_1h",
    "amount_z",
    "new_device",
    "new_city",
    "speed_kmh",
    "secs_since_last",
    "new_payee",
    "payee_in_degree_24h",
    "payee_inflow_24h",
    "payee_txn_count",
    "payee_passthrough_24h",
    "payer_secs_since_received",
    "payer_in_degree_24h",
    "fwd_ratio",
]
