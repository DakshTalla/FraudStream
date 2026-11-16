"""Rules engine, broker, alert storage and ring detection."""
import asyncio

from fraudstream import rules
from fraudstream.broker import InProcessBroker, make_broker
from fraudstream.config import FEATURE_NAMES, WEEK
from fraudstream.graph import RingTracker
from fraudstream.storage import AlertStore

from .conftest import make_txn


def calm_features(**overrides) -> dict:
    """A boring, legitimate-looking feature vector."""
    features = dict.fromkeys(FEATURE_NAMES, 0.0)
    features.update(amount=300.0, hour=14.0, secs_since_last=WEEK,
                    payer_secs_since_received=WEEK, payer_txn_count=50.0)
    features.update(overrides)
    return features


# ---- rules -------------------------------------------------------------------
def test_normal_payment_fires_no_rules():
    assert rules.evaluate(calm_features()) == []


def test_each_rule_fires_on_its_pattern():
    cases = {
        "impossible_travel": calm_features(speed_kmh=2000, new_city=1),
        "velocity_burst": calm_features(cnt_10m=7),
        "night_new_device_high_value": calm_features(is_night=1, new_device=1, amount=20_000),
        "mule_fan_in": calm_features(payee_in_degree_24h=4, payee_txn_count=4,
                                     payee_passthrough_24h=0.9),
        "rapid_pass_through": calm_features(payer_secs_since_received=120, fwd_ratio=0.9,
                                            amount=9000),
    }
    for expected, features in cases.items():
        assert [r.name for r in rules.evaluate(features)] == [expected]


def test_merchant_fan_in_is_not_a_mule():
    features = calm_features(is_p2m=1, payee_in_degree_24h=500, payee_txn_count=5)
    assert rules.evaluate(features) == []


# ---- broker ------------------------------------------------------------------
def test_in_process_broker_delivers_in_order():
    async def scenario():
        broker = InProcessBroker()
        for i in range(5):
            await broker.publish("t", {"i": i})
        batch = await anext(broker.subscribe("t"))   # one micro-batch
        return [message["i"] for message in batch]

    assert asyncio.run(scenario()) == [0, 1, 2, 3, 4]


def test_bounded_queue_applies_backpressure():
    async def scenario():
        broker = InProcessBroker(max_size=2)
        await broker.publish("t", {})
        await broker.publish("t", {})
        try:   # third publish must wait because nobody is consuming
            await asyncio.wait_for(broker.publish("t", {}), timeout=0.05)
        except asyncio.TimeoutError:
            return "blocked"
        return "accepted"

    assert asyncio.run(scenario()) == "blocked"


def test_memory_broker_is_used_when_requested():
    assert isinstance(make_broker("memory"), InProcessBroker)


# ---- storage -----------------------------------------------------------------
def test_alert_store_round_trip_and_feedback():
    store = AlertStore(":memory:")
    reasons = [{"feature": "new_device", "text": "Device never seen", "contribution": 2.1}]
    alert = store.add(make_txn().to_dict(), 0.97, "high", reasons, [])
    assert alert["status"] == "pending" and alert["reasons"] == reasons
    assert alert["txn"]["payer"] == "alice@upi"

    updated = store.set_status(alert["id"], "confirmed")
    assert updated["status"] == "confirmed" and updated["reviewed_at"] is not None
    assert store.counts() == {"pending": 0, "confirmed": 1, "dismissed": 0}
    assert store.list(status="pending") == []
    assert store.set_status(999, "dismissed") is None


# ---- ring detection ----------------------------------------------------------
def test_ring_tracker_finds_fan_in_cluster_and_assigns_roles():
    tracker = RingTracker()
    for victim in ("v1", "v2", "v3"):
        tracker.add(victim, "mule", 5000)
    tracker.add("mule", "boss", 14_000)
    tracker.add("mule2", "boss", 3000)
    tracker.add("v4", "mule2", 3200)

    (ring,) = tracker.rings()
    roles = {node["id"]: node["role"] for node in ring["nodes"]}
    assert roles["mule"] == "mule" and roles["boss"] == "collector"
    assert roles["v1"] == "sender"
    assert ring["total_amount"] == 35_200


def test_velocity_star_is_not_reported_as_a_ring():
    tracker = RingTracker()
    for i in range(8):                      # one payer spraying many payees
        tracker.add("victim", f"payee{i}", 500)
    assert tracker.rings() == []
