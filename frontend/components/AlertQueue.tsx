"use client";

import { useState } from "react";
import { formatClock, formatDay, formatRupees, shortId, titleCase } from "@/lib/api";
import type { Alert, AlertStatus } from "@/lib/types";
import Card, { EmptyState } from "./Card";

const TABS: { status: AlertStatus; label: string }[] = [
  { status: "pending", label: "Pending" },
  { status: "confirmed", label: "Confirmed fraud" },
  { status: "dismissed", label: "Dismissed" },
];

function SeverityBadge({ severity }: { severity: Alert["severity"] }) {
  const high = severity === "high";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium text-ink ${
        high ? "border-critical/60 bg-critical/15" : "border-warning/50 bg-warning/10"
      }`}
    >
      <span className={`h-2 w-2 rounded-full ${high ? "bg-critical" : "bg-warning"}`} />
      {high ? "High" : "Medium"}
    </span>
  );
}

/** "Why was this flagged": SHAP reasons, each with a bar showing how strongly
 *  that feature pushed the score towards fraud (relative to the top reason). */
function Reasons({ alert }: { alert: Alert }) {
  const strongest = Math.max(...alert.reasons.map((r) => r.contribution), 0.001);
  return (
    <ul className="mt-3 space-y-1.5">
      {alert.reasons.map((reason) => (
        <li key={reason.feature} className="flex items-center gap-3 text-xs">
          <span className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-raised">
            <span
              className="block h-full rounded-full bg-series-1"
              style={{ width: `${(reason.contribution / strongest) * 100}%` }}
            />
          </span>
          <span className="min-w-0 flex-1 text-ink-2">{reason.text}</span>
          <span className="tabular-nums text-muted" title="SHAP contribution in log-odds">
            +{reason.contribution.toFixed(2)}
          </span>
        </li>
      ))}
      {alert.rules.map((rule) => (
        <li key={rule.name} className="flex items-center gap-3 text-xs">
          <span className="w-16 shrink-0 rounded border border-line px-1 py-px text-center text-[10px] uppercase tracking-wide text-muted">
            Rule
          </span>
          <span className="min-w-0 flex-1 text-ink-2">{rule.description}</span>
        </li>
      ))}
    </ul>
  );
}

function AlertCard({
  alert,
  onReview,
}: {
  alert: Alert;
  onReview: (id: number, decision: Exclude<AlertStatus, "pending">) => void;
}) {
  const { txn } = alert;
  return (
    <article className="border-b border-line/70 px-4 py-3.5 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <SeverityBadge severity={alert.severity} />
        <span className="text-base font-semibold tabular-nums text-ink">
          {formatRupees(txn.amount)}
        </span>
        <span className="text-xs tabular-nums text-muted">
          score {alert.score.toFixed(2)} · {formatDay(txn.ts)}, {formatClock(txn.ts)} · {txn.city}
        </span>
      </div>
      <p className="mt-1.5 truncate font-mono text-xs text-ink-2">
        {shortId(txn.payer)} <span className="text-muted">→</span> {shortId(txn.payee)}
        <span className="ml-2 font-sans text-muted">
          {txn.payee_type === "merchant" ? "merchant" : "person"} · {txn.device_id}
        </span>
      </p>

      <Reasons alert={alert} />

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        {/* Shown for the demo only: a real bank would not know this yet. */}
        <span className="text-[11px] text-muted">
          Simulator label: {txn.is_fraud ? titleCase(txn.fraud_type) : "Legitimate"}
        </span>
        {alert.status === "pending" ? (
          <div className="flex gap-2">
            <button
              onClick={() => onReview(alert.id, "dismissed")}
              className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-ink-2 transition-colors hover:bg-raised"
            >
              Dismiss
            </button>
            <button
              onClick={() => onReview(alert.id, "confirmed")}
              className="rounded-md border border-critical/70 bg-critical/20 px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-critical/35"
            >
              Confirm fraud
            </button>
          </div>
        ) : (
          <span className="text-xs text-ink-2">
            {alert.status === "confirmed" ? "Confirmed as fraud" : "Dismissed as legitimate"}
          </span>
        )}
      </div>
    </article>
  );
}

export default function AlertQueue({
  alerts,
  onReview,
}: {
  alerts: Alert[];
  onReview: (id: number, decision: Exclude<AlertStatus, "pending">) => void;
}) {
  const [tab, setTab] = useState<AlertStatus>("pending");
  const visible = alerts.filter((a) => a.status === tab);
  const count = (status: AlertStatus) => alerts.filter((a) => a.status === status).length;

  return (
    <Card
      title="Alert queue"
      subtitle="Each alert lists the reasons behind its score"
      className="h-[34rem]"
      actions={
        <div className="flex rounded-lg border border-line p-0.5 text-xs" role="tablist">
          {TABS.map(({ status, label }) => (
            <button
              key={status}
              role="tab"
              aria-selected={tab === status}
              onClick={() => setTab(status)}
              className={`rounded-md px-2.5 py-1 transition-colors ${
                tab === status ? "bg-raised text-ink" : "text-muted hover:text-ink-2"
              }`}
            >
              {label} <span className="tabular-nums">{count(status)}</span>
            </button>
          ))}
        </div>
      }
    >
      {visible.length === 0 ? (
        <EmptyState>
          {tab === "pending" ? "No alerts waiting. New ones appear here live." : "Nothing here yet."}
        </EmptyState>
      ) : (
        <div className="scroll-quiet flex-1 overflow-y-auto">
          {visible.map((alert) => (
            <AlertCard key={alert.id} alert={alert} onReview={onReview} />
          ))}
        </div>
      )}
    </Card>
  );
}
