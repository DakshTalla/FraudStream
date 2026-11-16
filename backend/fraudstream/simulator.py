"""Generates realistic UPI traffic with four injected fraud patterns.

The simulator has its own clock (event time). Each transaction moves the clock
forward by a random gap, so 14 days of history can be generated in seconds for
training, and the same generator can be slowed down to N txns/sec for the live
demo. Fraud is injected as *episodes*: a short scripted sequence of payments
scheduled into the future and merged back into the stream in time order.

Legitimate noise is added on purpose (trips, new phones, big purchases) so the
model cannot win with a single trivial rule.
"""
from __future__ import annotations

import heapq
import itertools
import math
import random
from dataclasses import dataclass, field
from typing import Iterator

from .config import CITIES, DAY, HOUR, MINUTE, START_TS, haversine_km, ist_hour
from .schema import Transaction

UPI_LIMIT = 100_000.0  # per-transaction UPI cap in rupees

# Relative traffic volume for each hour of the day (IST). Nights are quiet.
HOUR_WEIGHT = [0.15, 0.1, 0.08, 0.08, 0.1, 0.2, 0.5, 0.8, 1.1, 1.3, 1.4, 1.4,
               1.5, 1.5, 1.3, 1.2, 1.3, 1.4, 1.6, 1.7, 1.6, 1.3, 0.8, 0.4]

FRAUD_TYPES = ["account_takeover", "velocity_burst", "mule_ring", "odd_hour_location"]
# Episode mix. Bursts and rings produce many payments per episode, so they get
# a lower episode weight to keep the four types balanced at transaction level.
FRAUD_WEIGHTS = [0.40, 0.10, 0.10, 0.40]


@dataclass
class User:
    vpa: str
    home_city: str
    city: str
    device: str
    amount_mu: float                 # mean of log(amount): personal spending level
    fav_merchants: list[str]
    friends: list[str] = field(default_factory=list)
    trip_until: float = 0.0          # while clock < trip_until the user is travelling
    last_ts: float = 0.0             # time of the user's previous payment


class Simulator:
    def __init__(self, seed: int = 42, n_users: int = 2000, n_merchants: int = 150,
                 txns_per_user_per_day: float = 6.0, episode_prob: float = 0.0016,
                 start_ts: float = START_TS):
        # WHY a private Random: a fixed seed makes every run reproducible,
        # so the metrics in the README can be regenerated exactly.
        self.rng = random.Random(seed)
        self.clock = start_ts
        self.episode_prob = episode_prob
        self.base_rate = n_users * txns_per_user_per_day / DAY / (sum(HOUR_WEIGHT) / 24)
        self._ids = itertools.count(1)
        self._new_accounts = itertools.count(n_users)
        self._pending: list[tuple[float, int, Transaction]] = []  # heap of future fraud txns
        self._city_names = list(CITIES)

        self.merchants = [f"shop{i:03d}@upi" for i in range(n_merchants)]
        # Zipf-like popularity: a few merchants get most of the traffic.
        merchant_weights = [1 / (i + 1) ** 0.8 for i in range(n_merchants)]
        self.users = []
        for i in range(n_users):
            home = self.rng.choice(self._city_names)
            self.users.append(User(
                vpa=f"user{i:05d}@upi", home_city=home, city=home,
                device=self._new_device(),
                amount_mu=self.rng.gauss(5.6, 0.6),  # median spend about Rs 270
                fav_merchants=self.rng.choices(self.merchants, merchant_weights, k=6),
            ))
        for user in self.users:
            user.friends = [u.vpa for u in self.rng.sample(self.users, 5)]
        # Some users are far more active than others.
        self._activity = list(itertools.accumulate(
            self.rng.lognormvariate(0, 0.7) for _ in self.users))
        # "Drop" accounts that account-takeover fraudsters cash out into.
        self.drop_accounts = [self._new_account() for _ in range(25)]

    # ---- public API ---------------------------------------------------------
    def stream(self) -> Iterator[Transaction]:
        """Endless stream of transactions in event-time order."""
        while True:
            gap = self.rng.expovariate(self.base_rate * HOUR_WEIGHT[ist_hour(self.clock)])
            self.clock += gap
            # Release scheduled fraud payments whose time has come.
            while self._pending and self._pending[0][0] <= self.clock:
                yield heapq.heappop(self._pending)[2]
            if self.rng.random() < self.episode_prob:
                self._start_episode()
            yield self._normal_txn()

    def take(self, n: int) -> list[Transaction]:
        return list(itertools.islice(self.stream(), n))

    # ---- helpers ------------------------------------------------------------
    def _new_device(self) -> str:
        return f"dev-{self.rng.getrandbits(32):08x}"

    def _new_account(self) -> str:
        return f"user{next(self._new_accounts):05d}@upi"

    def _txn(self, ts: float, payer: str, payee: str, amount: float, device: str,
             city: str, fraud_type: str = "") -> Transaction:
        payee_type = "merchant" if payee.startswith("shop") else "person"
        return Transaction(
            txn_id=f"T{next(self._ids):09d}", ts=ts, payer=payer, payee=payee,
            payee_type=payee_type, amount=round(min(max(amount, 1.0), UPI_LIMIT), 2),
            device_id=device, city=city, is_fraud=bool(fraud_type), fraud_type=fraud_type)

    def _schedule(self, txn: Transaction) -> None:
        heapq.heappush(self._pending, (txn.ts, next(self._ids), txn))

    def _could_have_travelled(self, user: User, dest: str) -> bool:
        flight_hours = haversine_km(user.city, dest) / 700 + 2   # +2h for airports
        return self.clock - user.last_ts >= flight_hours * HOUR

    def _usual_amount(self, user: User) -> float:
        return self.rng.lognormvariate(user.amount_mu, 0.8)

    def _next_night(self) -> float:
        """A timestamp between 1 AM and 4 AM IST, after the current clock."""
        ts = self.clock + self.rng.uniform(0, HOUR)
        while not 1 <= ist_hour(ts) < 4:
            ts += 20 * MINUTE
        return ts

    # ---- legitimate behaviour -----------------------------------------------
    def _normal_txn(self) -> Transaction:
        rng = self.rng
        user = rng.choices(self.users, cum_weights=self._activity)[0]

        # Legit noise 1: people travel, so "new city" alone is not fraud.
        # A real person needs flight time between cities, so a trip (or the
        # return home) only happens if enough time passed since the last payment.
        if self.clock >= user.trip_until:
            dest = user.home_city
            if rng.random() < 0.004:
                dest = rng.choice(self._city_names)
            if dest != user.city and self._could_have_travelled(user, dest):
                if dest != user.home_city:
                    user.trip_until = self.clock + rng.uniform(1, 4) * DAY
                user.city = dest
        user.last_ts = self.clock
        # Legit noise 2: people buy new phones, so "new device" alone is not fraud.
        if rng.random() < 0.0015:
            user.device = self._new_device()

        if rng.random() < 0.65:   # pay a merchant (P2M)
            payee = (rng.choice(user.fav_merchants) if rng.random() < 0.8
                     else rng.choice(self.merchants))
        else:                     # pay a person (P2P)
            payee = (rng.choice(user.friends) if rng.random() < 0.8
                     else rng.choice(self.users).vpa)
            if payee == user.vpa:
                payee = user.friends[0]
            # Legit noise 4: paying someone who just joined UPI. Without this,
            # "brand-new payee account" would be a perfect mule detector.
            if rng.random() < 0.03:
                payee = self._new_account()
                user.friends.append(payee)

        amount = self._usual_amount(user)
        # Legit noise 3: occasional big purchase (rent, fees, electronics).
        if rng.random() < 0.02:
            amount *= rng.uniform(5, 25)
        return self._txn(self.clock, user.vpa, payee, amount, user.device, user.city)

    # ---- fraud episodes -----------------------------------------------------
    def _start_episode(self) -> None:
        kind = self.rng.choices(FRAUD_TYPES, FRAUD_WEIGHTS)[0]
        getattr(self, f"_episode_{kind}")()

    def _episode_account_takeover(self) -> None:
        """Stolen credentials: new device, far-away city, drains to drop accounts."""
        rng = self.rng
        victim = rng.choice(self.users)
        device = self._new_device()
        city = rng.choice([c for c in self._city_names if c != victim.city])
        stealthy = rng.random() < 0.3   # a careful attacker: smaller, slower
        ts = self.clock
        for _ in range(rng.randint(2, 5)):
            ts += rng.uniform(10, 40) * MINUTE if stealthy else rng.uniform(20, 180)
            factor = rng.uniform(2, 5) if stealthy else rng.uniform(8, 40)
            amount = math.exp(victim.amount_mu) * factor
            self._schedule(self._txn(ts, victim.vpa, rng.choice(self.drop_accounts),
                                     amount, device, city, "account_takeover"))

    def _episode_velocity_burst(self) -> None:
        """Malware / collect-request scam: many small payments in seconds
        from the victim's OWN device and city, so only velocity gives it away."""
        rng = self.rng
        victim = rng.choice(self.users)
        ts = self.clock
        for _ in range(rng.randint(6, 15)):
            ts += rng.uniform(3, 25)
            payee = (rng.choice(self.merchants) if rng.random() < 0.5
                     else rng.choice(self.users).vpa)
            self._schedule(self._txn(ts, victim.vpa, payee, rng.uniform(100, 2500),
                                     victim.device, victim.city, "velocity_burst"))

    def _episode_mule_ring(self) -> None:
        """Scam victims pay 'mule' accounts, which forward the money to a
        collector within minutes. Each victim payment looks normal on its own
        (own device, own city), so only graph features can catch the ring."""
        rng = self.rng
        mules = [self._new_account() for _ in range(rng.randint(2, 4))]
        collector = self._new_account()
        mule_device = {m: self._new_device() for m in mules}
        mule_city = rng.choice(self._city_names)
        span = rng.uniform(1, 3) * HOUR
        for victim in rng.sample(self.users, rng.randint(5, 12)):
            ts = self.clock + rng.uniform(0, span)
            mule = rng.choice(mules)
            amount = rng.uniform(2000, 25000)
            self._schedule(self._txn(ts, victim.vpa, mule, amount,
                                     victim.device, victim.city, "mule_ring"))
            forward_ts = ts + rng.uniform(1, 10) * MINUTE
            self._schedule(self._txn(forward_ts, mule, collector,
                                     amount * rng.uniform(0.85, 0.97),
                                     mule_device[mule], mule_city, "mule_ring"))

    def _episode_odd_hour_location(self) -> None:
        """One large payment at 1-4 AM from a city the user is not in."""
        rng = self.rng
        victim = rng.choice(self.users)
        city = rng.choice([c for c in self._city_names if c != victim.city])
        device = victim.device if rng.random() < 0.5 else self._new_device()
        amount = math.exp(victim.amount_mu) * rng.uniform(5, 20)
        self._schedule(self._txn(self._next_night(), victim.vpa,
                                 rng.choice(self.users).vpa, amount, device, city,
                                 "odd_hour_location"))
