// Shapes of the JSON the backend sends. They mirror the Python side one-to-one
// (schema.py, storage.py, pipeline.py, train.py, graph.py).

export type Txn = {
  txn_id: string;
  ts: number; // event time, unix seconds (simulated clock)
  payer: string;
  payee: string;
  payee_type: "person" | "merchant";
  amount: number;
  device_id: string;
  city: string;
  is_fraud: boolean; // ground truth, known only because the data is simulated
  fraud_type: string;
};

export type Reason = { feature: string; value: number; contribution: number; text: string };
export type RuleHit = { name: string; description: string };
export type AlertStatus = "pending" | "confirmed" | "dismissed";

export type Alert = {
  id: number;
  txn: Txn;
  score: number;
  severity: "high" | "medium";
  reasons: Reason[];
  rules: RuleHit[];
  status: AlertStatus;
};

export type TickerRow = { txn: Txn; score: number; is_alert: boolean };

export type Stats = {
  processed: number;
  throughput_tps: number;
  latency_ms: { p50?: number; p95?: number; p99?: number };
  alerts: number;
  confusion: { tp: number; fp: number; fn: number; tn: number };
  live_precision: number;
  live_recall: number;
  review: Record<AlertStatus, number>;
  uptime_s: number;
};

export type Health = { status: string; broker: string; tps_target: number };

export type Quality = { precision: number; recall: number; false_positive_rate: number };

export type Metrics = {
  threshold: number;
  target_precision: number;
  pr_auc: number;
  roc_auc: number;
  fraud_rate: number;
  rows: { train: number; validation: number; test: number };
  model: Quality;
  rules_only: Quality;
  model_or_rules: Quality;
  recall_by_fraud_type: Record<string, number>;
  pr_curve: { recall: number; precision: number }[];
};

export type RingNode = {
  id: string;
  role: "sender" | "mule" | "collector" | "receiver";
  in_degree: number;
  out_degree: number;
};
export type RingEdge = { source: string; target: string; amount: number; count: number };
export type Ring = { nodes: RingNode[]; edges: RingEdge[]; total_amount: number };

// Messages pushed over the WebSocket.
export type ServerMessage =
  | { type: "txn"; txn: Txn; score: number; is_alert: boolean }
  | { type: "alert"; alert: Alert }
  | { type: "alert_update"; alert: Alert }
  | { type: "stats"; stats: Stats };
