from collections import Counter

from fraudstream.simulator import FRAUD_TYPES, UPI_LIMIT, Simulator


def test_same_seed_gives_identical_stream():
    a = [t.to_dict() for t in Simulator(seed=5).take(2000)]
    b = [t.to_dict() for t in Simulator(seed=5).take(2000)]
    assert a == b


def test_events_arrive_in_event_time_order():
    txns = Simulator(seed=5).take(20_000)
    assert all(earlier.ts <= later.ts for earlier, later in zip(txns, txns[1:]))


def test_fraud_is_rare_and_all_four_patterns_appear():
    txns = Simulator(seed=5).take(80_000)
    kinds = Counter(t.fraud_type for t in txns if t.is_fraud)
    fraud_rate = sum(kinds.values()) / len(txns)
    assert 0.002 < fraud_rate < 0.03          # heavily imbalanced, like real fraud
    assert set(kinds) == set(FRAUD_TYPES)


def test_amounts_respect_upi_limit_and_nobody_pays_themselves():
    for t in Simulator(seed=5).take(20_000):
        assert 1.0 <= t.amount <= UPI_LIMIT
        assert t.payer != t.payee


def test_mule_ring_money_flows_victim_to_mule_to_collector():
    txns = [t for t in Simulator(seed=5).take(80_000) if t.fraud_type == "mule_ring"]
    receivers = {t.payee for t in txns}
    forwarders = {t.payer for t in txns}
    assert receivers & forwarders, "some accounts must both receive and forward (mules)"
