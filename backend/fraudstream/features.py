"""Streaming feature engineering.

One `FeatureStore` holds a small piece of state per account and turns each
incoming transaction into a fixed feature vector in O(recent activity) time.

Two rules make this defensible:
1. Features are computed BEFORE the state is updated with the current
   transaction, so a feature only ever uses the past (no label leakage).
2. The SAME class builds the training set (replaying history) and scores live
   traffic, so there is no training/serving skew.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque

from .config import DAY, FEATURE_NAMES, HOUR, MINUTE, WEEK, haversine_km, ist_hour
from .schema import Transaction


class AccountState:
    """Everything we remember about one UPI id."""
    __slots__ = ("out", "inc", "n_out", "n_in", "log_mean", "log_m2", "devices",
                 "cities", "payees", "last_ts", "last_city", "last_in_ts")

    def __init__(self) -> None:
        self.out: deque[tuple[float, float, str]] = deque()  # (ts, amount, payee), last 24h
        self.inc: deque[tuple[float, float, str]] = deque()  # (ts, amount, payer), last 24h
        self.n_out = 0            # lifetime payments sent
        self.n_in = 0             # lifetime payments received
        self.log_mean = 0.0       # running mean of log(amount) sent
        self.log_m2 = 0.0         # running sum of squared deviations (Welford)
        self.devices: set[str] = set()
        self.cities: set[str] = set()
        self.payees: set[str] = set()
        self.last_ts: float | None = None
        self.last_city: str | None = None
        self.last_in_ts: float | None = None


def _evict(window: deque, now: float) -> None:
    """Drop events older than 24h. WHY: bounds memory per account and keeps
    every window query proportional to recent activity only."""
    while window and now - window[0][0] > DAY:
        window.popleft()


class FeatureStore:
    def __init__(self) -> None:
        self.accounts: defaultdict[str, AccountState] = defaultdict(AccountState)

    def compute_and_update(self, txn: Transaction) -> dict[str, float]:
        features = self.compute(txn)
        self.update(txn)
        return features

    # ---- read path ----------------------------------------------------------
    def compute(self, txn: Transaction) -> dict[str, float]:
        now = txn.ts
        payer = self.accounts[txn.payer]
        payee = self.accounts[txn.payee]
        for window in (payer.out, payer.inc, payee.out, payee.inc):
            _evict(window, now)

        # --- velocity: how busy has this payer been recently? ---
        cnt_10m = cnt_1h = 0
        sum_1h = 0.0
        payees_1h: set[str] = set()
        for ts, amount, to in reversed(payer.out):   # newest first
            age = now - ts
            if age > HOUR:
                break
            cnt_1h += 1
            sum_1h += amount
            payees_1h.add(to)
            if age <= 10 * MINUTE:
                cnt_10m += 1

        # --- amount z-score against the payer's own history ---
        # WHY log: rupee amounts are heavily right-skewed; in log space they
        # are roughly normal, so a z-score is meaningful.
        amount_z = 0.0
        if payer.n_out >= 3:
            std = math.sqrt(payer.log_m2 / (payer.n_out - 1))
            amount_z = (math.log(txn.amount) - payer.log_mean) / max(std, 0.25)
            amount_z = max(-10.0, min(10.0, amount_z))

        # --- device / location novelty ---
        has_history = payer.n_out > 0
        new_device = float(has_history and txn.device_id not in payer.devices)
        new_city = float(has_history and txn.city not in payer.cities)
        new_payee = float(has_history and txn.payee not in payer.payees)
        secs_since_last = WEEK
        speed_kmh = 0.0
        if payer.last_ts is not None:
            secs_since_last = min(now - payer.last_ts, WEEK)
            if payer.last_city != txn.city:
                # "Impossible travel": distance from the previous payment
                # divided by the time between them.
                hours = max(now - payer.last_ts, MINUTE) / HOUR
                speed_kmh = min(haversine_km(payer.last_city, txn.city) / hours, 5000.0)

        # --- graph features for mule rings ---
        # WHY skip merchants: a shop legitimately receives from thousands of
        # payers, so fan-in there is noise (and would cost a long scan).
        is_p2m = txn.payee_type == "merchant"
        payee_in_degree = payee_inflow = payee_passthrough = 0.0
        if not is_p2m:
            payee_in_degree = float(len({frm for _, _, frm in payee.inc}))
            payee_inflow = sum(a for _, a, _ in payee.inc)
            if payee_inflow > 0:
                # Share of received money the payee already sent onward.
                payee_outflow = sum(a for _, a, _ in payee.out)
                payee_passthrough = min(payee_outflow / payee_inflow, 2.0)

        # Is the PAYER behaving like a mule: just received money, now forwarding it?
        payer_inflow = sum(a for _, a, _ in payer.inc)
        payer_secs_since_received = WEEK
        if payer.last_in_ts is not None:
            payer_secs_since_received = min(now - payer.last_in_ts, WEEK)
        fwd_ratio = min(txn.amount / payer_inflow, 2.0) if payer_inflow > 0 else 0.0

        hour = ist_hour(now)
        return {
            "amount": txn.amount,
            "hour": float(hour),
            "is_night": float(hour < 5),
            "is_p2m": float(is_p2m),
            "payer_txn_count": float(payer.n_out),
            "cnt_10m": float(cnt_10m),
            "cnt_1h": float(cnt_1h),
            "cnt_24h": float(len(payer.out)),
            "sum_1h": sum_1h,
            "distinct_payees_1h": float(len(payees_1h)),
            "amount_z": amount_z,
            "new_device": new_device,
            "new_city": new_city,
            "speed_kmh": speed_kmh,
            "secs_since_last": secs_since_last,
            "new_payee": new_payee,
            "payee_in_degree_24h": payee_in_degree,
            "payee_inflow_24h": payee_inflow,
            "payee_txn_count": float(payee.n_in),
            "payee_passthrough_24h": payee_passthrough,
            "payer_secs_since_received": payer_secs_since_received,
            "payer_in_degree_24h": float(len({frm for _, _, frm in payer.inc})),
            "fwd_ratio": fwd_ratio,
        }

    # ---- write path ---------------------------------------------------------
    def update(self, txn: Transaction) -> None:
        payer = self.accounts[txn.payer]
        payee = self.accounts[txn.payee]

        payer.out.append((txn.ts, txn.amount, txn.payee))
        payer.n_out += 1
        # Welford's online algorithm: update mean and variance in O(1)
        # without storing the full history.
        x = math.log(txn.amount)
        delta = x - payer.log_mean
        payer.log_mean += delta / payer.n_out
        payer.log_m2 += delta * (x - payer.log_mean)
        payer.devices.add(txn.device_id)
        payer.cities.add(txn.city)
        payer.payees.add(txn.payee)
        payer.last_ts = txn.ts
        payer.last_city = txn.city

        payee.n_in += 1
        if txn.payee_type != "merchant":
            payee.inc.append((txn.ts, txn.amount, txn.payer))
            payee.last_in_ts = txn.ts


def to_vector(features: dict[str, float]) -> list[float]:
    """Order a feature dict the way the model expects."""
    return [features[name] for name in FEATURE_NAMES]
