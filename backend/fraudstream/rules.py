"""A small, transparent rules engine that runs next to the ML model.

WHY keep rules when there is a model: rules are instantly explainable, work
from day one with zero training data, and encode hard policy ("this is
physically impossible"). The model catches the fuzzy combinations that rules
miss. Banks run both; so do we.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

Features = dict[str, float]


@dataclass(frozen=True)
class Rule:
    name: str
    description: str
    check: Callable[[Features], bool]


RULES: list[Rule] = [
    Rule("impossible_travel",
         "Payer appears in a never-seen city faster than a flight allows (> 900 km/h)",
         lambda f: f["speed_kmh"] > 900 and f["new_city"] == 1),
    Rule("velocity_burst",
         "6 or more payments from this payer in the last 10 minutes",
         lambda f: f["cnt_10m"] >= 6),
    Rule("night_new_device_high_value",
         "High-value payment at night from a device never seen for this payer",
         lambda f: f["is_night"] == 1 and f["new_device"] == 1 and f["amount"] >= 5000),
    Rule("mule_fan_in",
         "Young personal account received from 3+ payers in 24h and forwarded most of it",
         lambda f: f["is_p2m"] == 0 and f["payee_in_degree_24h"] >= 3
         and f["payee_txn_count"] <= 10 and f["payee_passthrough_24h"] >= 0.6),
    Rule("rapid_pass_through",
         "Payer forwards 70-100% of the money it received in the last 15 minutes",
         lambda f: f["payer_secs_since_received"] < 900 and 0.7 <= f["fwd_ratio"] <= 1.0
         and f["amount"] >= 2000),
]


def evaluate(features: Features) -> list[Rule]:
    """Return every rule that fires for this transaction."""
    return [rule for rule in RULES if rule.check(features)]
