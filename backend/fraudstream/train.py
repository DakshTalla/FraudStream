"""Train the fraud model on simulated history.

    python -m fraudstream.train

Steps: simulate history -> replay it through the SAME FeatureStore used live
-> split by TIME -> train XGBoost -> pick an alert threshold on validation ->
report honest numbers on a held-out test period -> save model + metrics.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import xgboost as xgb
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

from . import rules
from .config import FEATURE_NAMES, METRICS_PATH, MODEL_DIR, MODEL_PATH
from .features import FeatureStore, to_vector
from .simulator import FRAUD_TYPES, Simulator

TARGET_PRECISION = 0.90


def build_dataset(n_txns: int, seed: int):
    """Replay simulated history through the feature store.

    Returns X (features), y (labels), fraud type per row, and whether any
    hand-written rule fired (used as a baseline to compare the model against).
    """
    store = FeatureStore()
    X, y, kinds, rule_hits = [], [], [], []
    for txn in Simulator(seed=seed).take(n_txns):
        features = store.compute_and_update(txn)
        X.append(to_vector(features))
        y.append(int(txn.is_fraud))
        kinds.append(txn.fraud_type)
        rule_hits.append(bool(rules.evaluate(features)))
    return (np.asarray(X, dtype=np.float32), np.asarray(y), np.asarray(kinds),
            np.asarray(rule_hits))


def pick_threshold(y_true, scores) -> float:
    """Lowest threshold whose validation precision is still >= TARGET_PRECISION.

    WHY: analysts can only review so many alerts. We fix how trustworthy an
    alert must be (precision) and then catch as much fraud as possible
    (recall) under that constraint. Falls back to best F1 if unreachable.
    """
    precision, recall, thresholds = precision_recall_curve(y_true, scores)
    ok = np.where(precision[:-1] >= TARGET_PRECISION)[0]
    if len(ok):
        return float(thresholds[ok[0]])   # thresholds ascend, so first = max recall
    f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-9)
    return float(thresholds[int(np.argmax(f1[:-1]))])


def confusion(y_true, flagged) -> dict:
    tp = int(np.sum(flagged & (y_true == 1)))
    fp = int(np.sum(flagged & (y_true == 0)))
    fn = int(np.sum(~flagged & (y_true == 1)))
    tn = int(np.sum(~flagged & (y_true == 0)))
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(tp / max(tp + fp, 1), 4),
        "recall": round(tp / max(tp + fn, 1), 4),
        "false_positive_rate": round(fp / max(fp + tn, 1), 5),
    }


def train(n_txns: int = 250_000, seed: int = 42) -> dict:
    started = time.time()
    X, y, kinds, rule_hits = build_dataset(n_txns, seed)
    print(f"Simulated {n_txns:,} txns, fraud rate {y.mean():.3%} "
          f"({time.time() - started:.1f}s)")

    # WHY a time split and not a random split: in production the model always
    # predicts the future from the past. A random split would leak later
    # behaviour of the same fraud episode into training and inflate metrics.
    # The first 10% is "burn-in": account histories are still empty there, so
    # the features are not representative, and we drop it.
    burn_in, train_end, val_end = (int(n_txns * p) for p in (0.10, 0.70, 0.85))
    X_tr, y_tr = X[burn_in:train_end], y[burn_in:train_end]
    X_val, y_val = X[train_end:val_end], y[train_end:val_end]
    X_te, y_te = X[val_end:], y[val_end:]

    # WHY scale_pos_weight: fraud is < 1% of rows. Without re-weighting, the
    # loss is dominated by legit rows and the model learns to say "legit".
    pos_weight = float((y_tr == 0).sum() / max((y_tr == 1).sum(), 1))
    model = xgb.XGBClassifier(
        n_estimators=400, max_depth=5, learning_rate=0.08,
        subsample=0.8, colsample_bytree=0.8, scale_pos_weight=pos_weight,
        tree_method="hist", n_jobs=4, random_state=seed,
        eval_metric="aucpr",          # PR-AUC, not accuracy: see STUDY_GUIDE
        early_stopping_rounds=30,     # stop when validation PR-AUC stops improving
    )
    model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)

    threshold = pick_threshold(y_val, model.predict_proba(X_val)[:, 1])
    scores = model.predict_proba(X_te)[:, 1]
    flagged = scores >= threshold
    te_kinds, te_rules = kinds[val_end:], rule_hits[val_end:]

    # The raw curve has one point per distinct score (tens of thousands, almost
    # all bunched at recall ~ 1). Resample it at 201 evenly spaced recall values
    # so the dashboard gets a small, evenly detailed curve.
    precision, recall, _ = precision_recall_curve(y_te, scores)
    grid = np.linspace(0, 1, 201)
    curve = np.interp(grid, recall[::-1], precision[::-1])   # interp needs ascending x
    importance = model.get_booster().get_score(importance_type="gain")
    metrics = {
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "feature_names": FEATURE_NAMES,
        "threshold": round(threshold, 4),
        "target_precision": TARGET_PRECISION,
        "rows": {"train": len(y_tr), "validation": len(y_val), "test": len(y_te)},
        "fraud_rate": round(float(y.mean()), 5),
        "best_iteration": int(model.best_iteration),
        "pr_auc": round(float(average_precision_score(y_te, scores)), 4),
        "roc_auc": round(float(roc_auc_score(y_te, scores)), 4),
        "model": confusion(y_te, flagged),
        "rules_only": confusion(y_te, te_rules),
        "model_or_rules": confusion(y_te, flagged | te_rules),
        "recall_by_fraud_type": {
            kind: round(float(flagged[te_kinds == kind].mean()), 4)
            for kind in FRAUD_TYPES if (te_kinds == kind).any()
        },
        "pr_curve": [{"recall": round(float(r), 3), "precision": round(float(p), 4)}
                     for r, p in zip(grid, curve)],
        "feature_importance": sorted(
            ({"feature": FEATURE_NAMES[int(k[1:])], "gain": round(v, 2)}
             for k, v in importance.items()),
            key=lambda item: -item["gain"]),
    }

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model.get_booster().save_model(str(MODEL_PATH))
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train the FraudStream model")
    parser.add_argument("--txns", type=int, default=250_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    result = train(args.txns, args.seed)
    summary = {k: result[k] for k in ("threshold", "pr_auc", "roc_auc", "model",
                                      "rules_only", "model_or_rules",
                                      "recall_by_fraud_type", "rows")}
    print(json.dumps(summary, indent=2))
    print("Top features:", [f["feature"] for f in result["feature_importance"][:6]])
