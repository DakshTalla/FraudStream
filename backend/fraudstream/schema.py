"""The one message type that flows through the whole pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass(slots=True)
class Transaction:
    txn_id: str
    ts: float            # EVENT time: when the payment happened (simulated clock)
    payer: str           # UPI id (VPA) of the sender
    payee: str           # UPI id of the receiver
    payee_type: str      # "person" (P2P) or "merchant" (P2M)
    amount: float        # rupees
    device_id: str
    city: str
    # Ground truth. Only a simulator knows this; a real bank learns it days
    # later from customer complaints. The model NEVER sees these two fields.
    is_fraud: bool = False
    fraud_type: str = ""
    # PROCESSING-time stamp (wall clock) set when the event is published.
    # Used only to measure end-to-end latency, never as a feature.
    emitted_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Transaction":
        # WHY filter: tolerate extra keys so an older consumer does not crash
        # when a newer producer adds a field (schema evolution).
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
