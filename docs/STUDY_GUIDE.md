# FraudStream study guide

Read this before an interview. It explains every idea the project uses, why each
decision was made, and what each file does. Numbers quoted here come from
`backend/models/metrics.json` (written by `train.py`) and `docs/benchmark.json`
(written by `benchmark.py`), so you can regenerate all of them.

Contents

1. [The project in 60 seconds](#1-the-project-in-60-seconds)
2. [Streaming concepts](#2-streaming-concepts)
3. [The fraud patterns and the features that catch them](#3-the-fraud-patterns-and-the-features-that-catch-them)
4. [Class imbalance](#4-class-imbalance)
5. [Choosing metrics](#5-choosing-metrics)
6. [Explanations with SHAP](#6-explanations-with-shap)
7. [Four real bugs and what they taught me](#7-four-real-bugs-and-what-they-taught-me)
8. [File-by-file walkthrough](#8-file-by-file-walkthrough)
9. [Interview questions and answers](#9-interview-questions-and-answers)
10. [Honest limitations and next steps](#10-honest-limitations-and-next-steps)

---

## 1. The project in 60 seconds

> "FraudStream scores UPI-style payments for fraud in real time. A simulator
> produces transactions with four injected fraud patterns. Each event goes
> through a message broker (Redpanda, which speaks the Kafka protocol) to a
> scorer. The scorer keeps per-account state in memory, turns each event into
> 23 features, and runs an XGBoost model plus five hand-written rules. Every
> alert carries SHAP reasons and is stored in SQLite. A Next.js dashboard shows
> the live stream, the alert queue, suspected mule rings as a graph, and model
> quality, and lets an analyst confirm or dismiss each alert."

The data flow in one line:

```
simulator -> broker topic -> feature store -> rules + model -> SHAP -> SQLite -> WebSocket -> dashboard
```

---

## 2. Streaming concepts

### Batch versus stream

A batch job reads a finished file and produces a result. A stream job never
finishes: events arrive one at a time and each must be handled within
milliseconds, using only what has been seen so far. Fraud detection must be a
stream job because the payment has to be judged before or just after the money
moves.

### Event time versus processing time

- **Event time** is when the payment happened. In the code this is `txn.ts`.
- **Processing time** is when our system handled it. In the code this is the
  wall clock, and `txn.emitted_at` records when the event was published.

All features use event time. "Payments in the last 10 minutes" means 10 minutes
on the payment's own clock. This is why the simulator can generate two weeks of
history in a few seconds for training and still give the same features as the
live stream. Processing time is used for one thing only: measuring latency
(`time scored - emitted_at`).

### Windows

A window is "the events inside some time range". FraudStream uses **sliding
windows** that end at the current event: last 10 minutes, last hour, last 24
hours. Each account keeps a `deque` of its recent payments. Before reading, old
entries are dropped from the left (`_evict` in `features.py`), so the deque
never holds more than 24 hours and memory per account stays bounded.

Other window types to know by name: **tumbling** (fixed, non-overlapping, for
example every 5 minutes) and **session** (closed after a gap of inactivity).

### State

A feature like "has this payer used this device before" needs memory of the
past. That memory is called state. Here it is a Python dictionary from account
id to an `AccountState` object. In a production system the same state would
live in Redis or in a stream processor such as Flink, so that it survives a
restart and can be shared by several scorer processes.

### Running statistics (Welford's algorithm)

The amount z-score needs each payer's mean and standard deviation. Storing every
past amount would grow forever. Welford's algorithm updates the mean and the
variance from just three numbers (count, mean, sum of squared deviations) in
constant time per event. See `FeatureStore.update`.

### Topics, partitions, keys and ordering

Kafka stores events in a **topic**, which is split into **partitions**. Order is
guaranteed only inside one partition. The producer sets the message **key** to
the payer id (`KafkaBroker.publish`), and Kafka sends equal keys to the same
partition, so all payments of one payer stay in order. That matters because
velocity features are wrong if events are processed out of order.

### Consumer groups and offsets

A consumer **group** shares the work of reading a topic and remembers its
position (the committed **offset**). If the scorer restarts, it continues from
that offset instead of starting over. FraudStream deliberately uses a new topic
for every run, because each run restarts the simulated clock (see bug 4 below).

### Delivery guarantees

- **At most once**: an event may be lost, never repeated.
- **At least once**: an event is never lost, but may be processed twice after a
  crash. This is what the Kafka path gives here (offsets are committed
  automatically after reading).
- **Exactly once**: needs transactions or idempotent processing.

Effect of a duplicate in this project: a payment would be counted twice in the
velocity windows and could raise a duplicate alert. The fix would be to
remember recently seen `txn_id` values and skip repeats (idempotency).

### Backpressure

If the producer is faster than the consumer, something must give. The
in-process broker uses a **bounded** queue of 10,000 events: when it is full,
`publish` waits. The producer is slowed down instead of memory growing until
the process crashes. There is a test for this
(`test_bounded_queue_applies_backpressure`).

### Micro-batching

The scorer does not wait to fill a batch. It takes one event, then also takes
whatever else is **already waiting** (up to 256), and scores them in one model
call. Under light load a batch is one event, so nothing is delayed. Under heavy
load batches grow automatically and throughput rises. This single change took
the in-process pipeline from about 2,070 to about 20,600 transactions per second
(bug 1 below). You can reproduce both numbers: `python -m fraudstream.benchmark`
with and without `--no-batching`.

### Warm-up by replay

A scorer that starts with empty state sees every device and city as "new" and
would alert on everything. Before going live, the API replays 340,000 simulated
transactions through `FeatureStore.update` to build state. In production this
is done by replaying the Kafka topic from an older offset, which is one of the
main reasons to use a log-based broker.

---

## 3. The fraud patterns and the features that catch them

| Pattern | What the simulator does | Features that expose it |
|---|---|---|
| Account takeover | New device, far-away city, 2 to 5 large payments to "drop" accounts within minutes. 30% are "stealthy": smaller and slower. | `new_device`, `new_city`, `speed_kmh`, `amount_z`, `new_payee`, `sum_1h` |
| Velocity burst | 6 to 15 small payments a few seconds apart from the victim's own device and city. | `cnt_10m`, `cnt_1h`, `distinct_payees_1h`, `secs_since_last` |
| Mule ring | 5 to 12 victims each pay one of 2 to 4 new "mule" accounts, which forward 85 to 97% to a collector within 10 minutes. | `payee_in_degree_24h`, `payee_inflow_24h`, `payee_passthrough_24h`, `payer_secs_since_received`, `fwd_ratio`, `payer_in_degree_24h` |
| Odd hour and location | One large payment at 1 to 4 AM from another city. | `is_night`, `hour`, `new_city`, `amount_z` |

**Legitimate noise is injected on purpose** so that no single feature is a
perfect detector: people travel (with realistic flight time), buy new phones,
make occasional large purchases, and pay brand-new accounts.

### Why graph features

In a mule ring every victim pays from their own phone in their own city. Nothing
about the payer looks wrong. The signal is on the receiving side: a young
account that suddenly receives from several unrelated people and forwards the
money almost immediately. `payee_in_degree_24h` is the number of distinct payers
(the in-degree of the payee node in the payment graph), and `fwd_ratio` measures
how much of the received money is being passed on.

Merchants are excluded from these features. A shop legitimately receives from
thousands of payers, so fan-in there means nothing, and scanning a long list on
every event would be slow.

### No leakage, no skew

Two rules make the features trustworthy:

1. **Compute, then update.** `compute_and_update` reads the state first and adds
   the current payment afterwards. A feature never includes the payment it
   describes. Test: `test_no_leakage_current_txn_is_not_counted_in_its_own_features`.
2. **One code path.** `train.py` builds the training set by replaying history
   through the same `FeatureStore` class the live scorer uses. If training and
   serving computed features with different code, small differences would
   silently hurt the model (training/serving skew).

---

## 4. Class imbalance

Fraud is 0.77% of transactions in the simulated history.

**Why accuracy is useless.** A model that always answers "legitimate" is 99.23%
accurate and catches nothing.

**What the project does about it:**

- `scale_pos_weight = negatives / positives` in XGBoost. Each fraud row then
  counts as much in the loss as about 130 legitimate rows, so the trees are
  forced to pay attention to the rare class.
- `eval_metric="aucpr"` with early stopping, so training stops when
  precision-recall quality on validation stops improving, not when accuracy does.
- The decision threshold is **chosen**, not left at 0.5 (see section 5).

**Alternatives and why they were not used:**

- *Undersampling* legitimate rows: throws away data and changes the base rate.
- *SMOTE* (synthetic minority oversampling): invents fraud rows by interpolating
  between real ones. That does not respect time order, and an interpolated
  "payment" may be a combination of features that cannot really occur.
- *Class weights* (what was chosen): keeps all data and is one parameter.

**Side effect to mention:** re-weighting makes the model's probabilities too high
compared with the true fraud rate (it is no longer calibrated). That is fine
here because the score is only compared with a threshold, but if the score were
used as a real probability (for example, expected loss = probability x amount)
it would need calibration first (Platt scaling or isotonic regression).

---

## 5. Choosing metrics

|  | Predicted fraud | Predicted legit |
|---|---|---|
| **Actually fraud** | True positive (TP) | False negative (FN) |
| **Actually legit** | False positive (FP) | True negative (TN) |

- **Precision** = TP / (TP + FP). Of the alerts raised, how many were real
  fraud. Low precision wastes analyst time and blocks honest customers.
- **Recall** = TP / (TP + FN). Of all real fraud, how much was caught. Low
  recall means money lost.
- **False-positive rate** = FP / (FP + TN). Share of honest payments flagged.

**Why PR-AUC rather than ROC-AUC.** ROC plots recall against the false-positive
rate. With 99% legitimate rows, even thousands of false alarms are a tiny
false-positive rate, so ROC-AUC looks excellent for mediocre models. The
precision-recall curve has precision on one axis, which is hit directly by
every false alarm. A random model scores PR-AUC equal to the fraud rate (about
0.008 here) but ROC-AUC 0.5, so PR-AUC is the stricter number.

**How the threshold is chosen** (`pick_threshold` in `train.py`). Analysts can
review only a limited number of alerts. So the rule is: on the validation set,
take the lowest threshold whose precision is still at least 90%. That gives the
most recall for an acceptable alert quality. The threshold is picked on
validation and reported on the test set, so the test numbers are not tuned.

**Why a time split.** Rows are split by time: first 10% dropped as burn-in, then
60% train, 15% validation, 15% test. A random split would put payments from the
same fraud episode in both train and test and inflate the score. In production
the model always predicts the future from the past, so the test must too.

**Results on the held-out test period** (60,000 transactions):

| Detector | Precision | Recall | False-positive rate |
|---|---|---|---|
| XGBoost model | 92.8% | 94.0% | 0.05% |
| Rules only | 96.2% | 26.6% | 0.01% |
| Model or rules (what runs live) | 91.9% | 94.0% | 0.05% |

PR-AUC 0.961, ROC-AUC 0.997. Recall by pattern: account takeover 94%, velocity
burst 88%, mule ring 100%, odd hour and location 100%.

Reading the table: rules are precise but catch only a quarter of fraud. The
model catches most of it. Adding the rules to the model adds no recall on this
data and costs about one point of precision, so on numbers alone the rules are
not needed. They are kept because they explain themselves in one sentence, work
with no training data, and express policy that must hold even if the model is
wrong.

**A second, stricter check.** The live stream uses a different random seed from
training, so it is a fresh population of users and fraud episodes. Over 60,000
live events the pipeline measured 92.3% precision and 95.0% recall
(`docs/benchmark.json`), consistent with the offline numbers.

---

## 6. Explanations with SHAP

An analyst will not act on "score 0.97" alone, and regulators expect a reason
for blocking a payment. SHAP (SHapley Additive exPlanations) splits one
prediction into a contribution per feature such that

```
base value + sum of contributions = the model's raw output (log-odds)
```

A positive contribution pushed this payment towards fraud, a negative one
towards legitimate. The idea comes from Shapley values in game theory: a fair
way to share a payout among players, averaged over every order in which the
players could join.

**TreeSHAP** computes these exactly for tree models in polynomial time instead
of trying every feature subset. It is built into XGBoost
(`predict(pred_contribs=True)`), which is what `FraudModel.shap_values` calls.

The dashboard shows the top four positive contributions per alert, each turned
into a sentence (`REASON_TEXT` in `model.py`). SHAP runs only for alerts, under
1% of traffic, because it is the slowest step.

The additivity property is tested
(`test_shap_contributions_add_up_to_the_model_output`), so the reasons shown are
provably the model's own arithmetic, not a separate approximation.

One caution worth saying out loud: SHAP explains **the model**, not the world.
"New device contributed +2.1" means the model leaned on that feature, not that
the new device caused the fraud.

---

## 7. Four real bugs and what they taught me

These happened while building the project. They make better interview stories
than the features that worked first time.

**1. Throughput was stuck at about 2,000 transactions per second.**
Profiling with `cProfile` showed 85% of the time inside XGBoost's
`inplace_predict`, called once per transaction. Each call has a fixed overhead
of about half a millisecond that has nothing to do with the trees. Fix: adaptive
micro-batching, one model call for everything already waiting. Result: about
20,600 per second, ten times more, with median latency unchanged at 0.5 ms.
(The first measurements, taken while the laptop was busy, were 928 and 7,977:
lower absolute numbers, same ratio.) Lesson: measure before
optimising. I would have guessed the Python feature code was the slow part; it
was 7% of the time.

**2. Live precision was 83% when the offline test said 93%.**
Same model, same code. The difference was state maturity: the test period comes
after 340,000 transactions of history, but the live scorer warmed up on only
150,000, so accounts looked "younger" than anything the model was tested on.
Matching the warm-up length restored 92 to 94% on two unseen seeds. Lesson:
training/serving skew is not only about code; the state behind the features has
to match too.

**3. The explanations did not add up.**
The first version used the `shap` package. The additivity test failed: the
package reported a base value of 0 for an XGBoost 3.x model, so base plus
contributions was off by a constant. Switching to XGBoost's built-in TreeSHAP
fixed it and removed a heavy dependency. Lesson: test the mathematical property
you rely on, not just "it returns numbers".

**4. On Redpanda the dashboard showed 20-second latency.**
The consumer group had a committed offset from an earlier run, so the scorer
began by replaying a backlog of old events, which were also out of order in
event time relative to the new run. Fix: a new topic and group for each run,
read from the beginning. Lesson: consumer groups resume where they left off.
That is the feature you want in production and a surprise in a demo that
restarts its clock.

A smaller one: the SHAP fix and the batching fix were both found by tests or
measurements written before the dashboard existed. Background tasks also log
their own crashes now (`log_task_failure` in `api.py`), because an exception
inside an asyncio task is silent by default.

---

## 8. File-by-file walkthrough

### Backend (`backend/fraudstream/`)

| File | What it does | The one thing to remember |
|---|---|---|
| `config.py` | Paths, settings from environment variables, city coordinates, `FEATURE_NAMES`. | `FEATURE_NAMES` is the single source of column order for training and serving. |
| `schema.py` | The `Transaction` dataclass with `to_dict` / `from_dict`. | `ts` is event time, `emitted_at` is processing time. `from_dict` ignores unknown keys so adding a field does not break old consumers. |
| `simulator.py` | Generates users, merchants and an endless time-ordered stream; injects the four fraud episodes. | Fraud payments are scheduled into the future on a heap and merged back in time order. A fixed seed makes every run reproducible. |
| `features.py` | `FeatureStore`: per-account state and the 23 features. | Compute before update (no leakage); evict to 24 hours (bounded memory); Welford for running statistics. |
| `rules.py` | Five rules as data: name, description, a one-line check. | Adding a rule is adding one entry to a list. |
| `model.py` | Loads the model, `score_batch`, `shap_values`, `explain`. | One batched model call; SHAP only for alerts; reasons are sentences. |
| `train.py` | Simulate, build features, time split, train, pick threshold, write `model.json` and `metrics.json`. | Time split, `scale_pos_weight`, threshold chosen on validation for 90% precision. |
| `broker.py` | `Broker` interface, `InProcessBroker`, `KafkaBroker`, `make_broker`. | The pipeline depends on the interface, so Kafka and the in-memory queue are interchangeable. `subscribe` yields micro-batches. |
| `storage.py` | `AlertStore`: SQLite table of alerts and analyst decisions. | Alerts are under 1% of traffic, so a simple synchronous database is enough. |
| `graph.py` | `RingTracker`: graph of recent alerted payments, connected components, node roles. | A ring needs fan-in: one payer spraying many payees is a star, not a ring. |
| `pipeline.py` | `Pipeline`: features, model, rules, decision, alert, live confusion matrix, latency statistics. | Knows nothing about Kafka or HTTP, so the API, the benchmark and the tests all reuse it. |
| `api.py` | FastAPI app, background producer and scorer tasks, REST endpoints, WebSocket hub. | Producer and scorer talk only through the broker, like two separate services. |
| `benchmark.py` | Measures maximum throughput and latency percentiles at a fixed rate. | Latency is end to end: publish, broker, features, model, rules, SHAP, SQLite. |

### Tests (`backend/tests/`, 29 tests)

| File | What it proves |
|---|---|
| `test_simulator.py` | Same seed gives the same stream; events are time-ordered; fraud is rare and all four patterns occur; mules both receive and forward. |
| `test_features.py` | No leakage; window counts; 24-hour eviction; novelty flags; impossible-travel speed; z-score matches a hand calculation; mule graph features; merchants excluded. |
| `test_components.py` | Each rule fires on its pattern and stays quiet on normal payments; broker order and backpressure; alert storage and feedback; ring detection and roles. |
| `test_model_pipeline_api.py` | Training beats random by a wide margin; threshold meets the precision target; SHAP additivity; a scripted takeover is caught and a routine payment is not; quality on an unseen seed; the HTTP and WebSocket API end to end. |

### Frontend (`frontend/`)

| File | What it does |
|---|---|
| `app/layout.tsx`, `app/page.tsx` | Page shell. The page renders one client component, `Dashboard`. |
| `app/globals.css` | Design tokens (every colour used) and the row-flash animation. |
| `lib/types.ts` | TypeScript types that mirror the backend JSON. |
| `lib/api.ts` | API address, `getJSON`, `sendFeedback`, number and time formatters. |
| `lib/useFraudStream.ts` | The single data hook: WebSocket with auto-reconnect, REST snapshot, ring polling, and a buffer that updates the ticker about three times a second instead of on every message. |
| `components/Dashboard.tsx` | Header and page grid. |
| `components/StatTiles.tsx` | Six headline numbers and a throughput sparkline. |
| `components/TransactionTicker.tsx` | Live table of payments with risk score. |
| `components/AlertQueue.tsx` | Alerts with SHAP reasons, fired rules and Confirm / Dismiss buttons; tabs by review status. |
| `components/MuleGraph.tsx` | Force-directed graph of a suspected ring (layout by `d3-force`, drawn as SVG). |
| `components/ModelMetrics.tsx` | Precision-recall curve with the operating point, model-versus-rules table, recall per fraud pattern. |
| `components/Card.tsx` | Shared panel and empty-state components. |

### Root

| File | What it does |
|---|---|
| `start.ps1` | One command: venv, packages, training, optional Redpanda, both servers, opens the browser. |
| `docker-compose.yml` | Single-node Redpanda on `localhost:19092`. |

---

## 9. Interview questions and answers

**Q1. Walk me through what happens to one transaction.**
The simulator creates it and stamps `emitted_at`. It is published to the broker
keyed by payer. The scorer receives it in a micro-batch, the feature store
computes 23 features from the payer's and payee's state and then updates that
state, XGBoost scores the batch, and five rules are checked. If the score is
above the threshold or a rule fires, SHAP reasons are computed, the alert is
written to SQLite and pushed to dashboards over the WebSocket.

**Q2. Why Kafka or Redpanda instead of calling the scorer directly?**
Decoupling (producer and scorer can fail, restart and scale independently), a
durable ordered log that can be replayed to rebuild state or test a new model on
old traffic, and backpressure. Redpanda was chosen over Apache Kafka because it
is one small container with no ZooKeeper or JVM, and speaks the same protocol.

**Q3. Why is there an in-process queue as well?**
So the project and its tests run on a laptop without Docker. Both brokers
implement the same `Broker` interface, so the pipeline code is identical.

**Q4. How do you avoid data leakage?**
Features are computed before the state is updated with the current event; the
split is by time; the threshold is chosen on validation and reported on test;
the label fields are never in `FEATURE_NAMES`.

**Q5. Why XGBoost and not a neural network?**
The data is tabular with 23 hand-built features. Gradient-boosted trees are the
strongest baseline for that, train in seconds on a CPU, need no feature scaling,
score in well under a millisecond per row when batched, and have exact SHAP
values. A neural network would add cost without an obvious gain here.

**Q6. Why not accuracy?**
Fraud is 0.77% of rows. "Always legitimate" is 99.23% accurate and useless.

**Q7. Precision or recall: which matters more?**
It depends on the cost of each error. A missed fraud loses money; a false alarm
costs analyst time and annoys a customer. This project fixes precision at about
90% (alerts are worth reading) and maximises recall under that. A bank that
blocks payments automatically would want higher precision; one that only sends
an SMS could accept lower.

**Q8. Your numbers look very good. Why should I believe them?**
They are on simulated data, and I say so. What I can defend is the method: time
split, threshold chosen on validation, a second check on a different random
seed, and legitimate noise added so no single feature gives the answer away.
Real fraud is harder and changes over time, so real numbers would be lower.

**Q9. What is concept drift and how would you handle it?**
Fraudsters change tactics, so the relationship between features and fraud
changes. Monitor live precision from analyst feedback and the distribution of
scores and features; retrain on a schedule with recent labelled data; keep
rules for brand-new patterns the model has not learned yet. The Confirm and
Dismiss buttons already store the labels needed for retraining.

**Q10. In production labels arrive late. What changes?**
The live precision and recall tiles exist only because the simulator knows the
truth. A real system sees confirmed fraud days or weeks later (chargebacks,
complaints), and only sees false negatives if a customer reports them. You would
measure precision from analyst decisions and estimate recall from later reports.

**Q11. How would you scale this to 100,000 transactions per second?**
More partitions keyed by payer and one scorer per partition; move state from a
Python dict to Redis or a stream processor's keyed state; the payee-side graph
features need a second keying by payee (or a shared store), because payer and
payee may land on different partitions. The model is not the bottleneck once
calls are batched.

**Q12. What happens if the scorer crashes?**
In this demo the in-memory state is lost and rebuilt by warm-up on restart. In
production you would checkpoint state or rebuild it by replaying the log from a
saved offset, and consume with at-least-once delivery plus de-duplication by
`txn_id`.

**Q13. What is the latency made of?**
In-process: about 0.5 ms median, mostly feature computation and the model call.
Through Redpanda: about 18 ms median (24 ms at p99), dominated by the producer's 5 ms batching
window, a network round trip to the broker container and the consumer poll.
SHAP and the SQLite write only happen for alerts.

**Q14. Why SQLite?**
Alerts are a tiny fraction of traffic, it needs no setup, and it is in the
standard library. The SQL is plain; moving to Postgres means changing the
connection and placeholders.

**Q15. Why WebSocket and not polling?**
The server pushes events as they happen with no per-request overhead. Slowly
changing data (the ring graph, offline metrics) is fetched over plain REST
because pushing it would add complexity for no benefit.

**Q16. How does the ring detection work?**
Alerted payments are edges in a directed graph. `networkx` finds weakly
connected components (connected if you ignore direction). A component is
reported if it has at least four accounts and some account receives from two or
more others. Roles come from degrees: receives and sends is a mule; receives
from several and sends nothing is a collector; only sends is a likely victim.

**Q17. What does scale_pos_weight do, exactly?**
It multiplies the gradient of every positive row. With a weight of about 130,
misclassifying one fraud costs the loss as much as misclassifying 130 legitimate
payments.

**Q18. What is the difference between a rule and a feature?**
A feature is a number describing the event. A rule is a decision written by a
person on top of features. The rules here reuse the same features the model
sees, so there is one feature pipeline, not two.

**Q19. How did you test a streaming system?**
The pipeline is a plain class with no I/O, so most tests feed it transactions
directly with fixed timestamps. The broker has its own tests. One end-to-end
test starts the real FastAPI app with the in-process broker and checks the
WebSocket, the alert list and the feedback endpoint.

**Q20. What would you do differently with more time?**
See the next section.

---

## 10. Honest limitations and next steps

- **Simulated data.** The fraud patterns were written by the same person who
  built the detector. The numbers show the pipeline works, not how it would do
  against real fraud.
- **State lives in one process.** No persistence, no sharing between scorers.
  Next step: Redis with a TTL per key.
- **Events are assumed to arrive in order.** There is no handling of late or
  out-of-order events (watermarks).
- **At-least-once without de-duplication** on the Kafka path.
- **Cold start.** New accounts have no history, and the mule rule over-fires
  when all accounts are young, which is why warm-up length matters.
- **Feedback is stored but not yet used.** The retraining loop that consumes
  analyst decisions is not built.
- **Uncalibrated scores.** Fine for thresholding, not for expected-loss maths.
- **No authentication** on the API, and CORS is open. Acceptable for a local
  demo, not for deployment.
