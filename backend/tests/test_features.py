import math

import pytest

from fraudstream.config import DAY, FEATURE_NAMES, HOUR
from fraudstream.features import FeatureStore, to_vector

from .conftest import make_txn

T0 = 1_000_000.0


def test_feature_dict_matches_model_column_list():
    features = FeatureStore().compute_and_update(make_txn())
    assert list(features) == FEATURE_NAMES
    assert len(to_vector(features)) == len(FEATURE_NAMES)


def test_no_leakage_current_txn_is_not_counted_in_its_own_features():
    store = FeatureStore()
    first = store.compute_and_update(make_txn(ts=T0))
    assert first["cnt_10m"] == 0 and first["payer_txn_count"] == 0
    second = store.compute_and_update(make_txn(ts=T0 + 10))
    assert second["cnt_10m"] == 1 and second["payer_txn_count"] == 1


def test_velocity_windows_count_only_recent_payments():
    store = FeatureStore()
    for offset in (0, 2 * HOUR, 2 * HOUR + 60, 2 * HOUR + 120):
        store.update(make_txn(ts=T0 + offset, payee=f"p{offset}@upi", amount=100))
    f = store.compute(make_txn(ts=T0 + 2 * HOUR + 180))
    assert f["cnt_10m"] == 3            # the three in the last few minutes
    assert f["cnt_1h"] == 3
    assert f["cnt_24h"] == 4            # includes the one from 2 hours ago
    assert f["sum_1h"] == 300
    assert f["distinct_payees_1h"] == 3


def test_events_older_than_24h_are_evicted():
    store = FeatureStore()
    store.update(make_txn(ts=T0))
    f = store.compute(make_txn(ts=T0 + DAY + 1))
    assert f["cnt_24h"] == 0
    assert f["payer_txn_count"] == 1    # lifetime count is kept
    assert len(store.accounts["alice@upi"].out) == 0


def test_device_city_and_payee_novelty():
    store = FeatureStore()
    store.update(make_txn(ts=T0))
    same = store.compute(make_txn(ts=T0 + 60))
    assert (same["new_device"], same["new_city"], same["new_payee"]) == (0, 0, 0)
    odd = store.compute(make_txn(ts=T0 + 60, device="dev-x", city="Delhi", payee="z@upi"))
    assert (odd["new_device"], odd["new_city"], odd["new_payee"]) == (1, 1, 1)


def test_impossible_travel_speed():
    store = FeatureStore()
    store.update(make_txn(ts=T0, city="Mumbai"))
    f = store.compute(make_txn(ts=T0 + 600, city="Delhi"))   # about 1150 km in 10 min
    assert f["speed_kmh"] > 900
    assert store.compute(make_txn(ts=T0 + 600, city="Mumbai"))["speed_kmh"] == 0


def test_amount_z_score_uses_running_log_statistics():
    store = FeatureStore()
    amounts = [100, 200, 400, 800]
    for i, amount in enumerate(amounts):
        store.update(make_txn(ts=T0 + i, amount=amount))
    logs = [math.log(a) for a in amounts]
    mean = sum(logs) / 4
    std = math.sqrt(sum((x - mean) ** 2 for x in logs) / 3)
    f = store.compute(make_txn(ts=T0 + 10, amount=50_000))
    assert f["amount_z"] == pytest.approx((math.log(50_000) - mean) / std)
    assert f["amount_z"] > 3


def test_mule_graph_features():
    store = FeatureStore()
    # three victims pay the mule, then the mule forwards almost everything
    for i, victim in enumerate(["v1@upi", "v2@upi", "v3@upi"]):
        store.update(make_txn(ts=T0 + i * 60, payer=victim, payee="mule@upi", amount=10_000))
    fourth = store.compute(make_txn(ts=T0 + 200, payer="v4@upi", payee="mule@upi"))
    assert fourth["payee_in_degree_24h"] == 3
    assert fourth["payee_inflow_24h"] == 30_000

    forward = store.compute(make_txn(ts=T0 + 300, payer="mule@upi", payee="boss@upi",
                                     amount=27_000))
    assert forward["payer_in_degree_24h"] == 3
    assert forward["fwd_ratio"] == pytest.approx(0.9)
    assert forward["payer_secs_since_received"] == 180


def test_merchants_are_excluded_from_graph_features():
    store = FeatureStore()
    for i in range(5):
        store.update(make_txn(ts=T0 + i, payer=f"u{i}@upi", payee="shop001@upi",
                              payee_type="merchant"))
    f = store.compute(make_txn(ts=T0 + 10, payee="shop001@upi", payee_type="merchant"))
    assert f["is_p2m"] == 1 and f["payee_in_degree_24h"] == 0
    assert f["payee_txn_count"] == 5
