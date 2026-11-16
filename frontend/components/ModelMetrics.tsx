"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatPercent, titleCase } from "@/lib/api";
import type { Metrics, Quality } from "@/lib/types";
import Card, { EmptyState } from "./Card";

const percentTick = (v: number) => `${Math.round(v * 100)}%`;
const AXIS_TICKS = [0, 0.25, 0.5, 0.75, 1];
const tickStyle = { fill: "var(--color-muted)", fontSize: 11 };

function CurveTooltip({ active, payload }: { active?: boolean; payload?: { payload: { recall: number; precision: number } }[] }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs shadow-lg">
      <p className="tabular-nums text-ink">Precision {formatPercent(point.precision)}</p>
      <p className="tabular-nums text-muted">at recall {formatPercent(point.recall)}</p>
    </div>
  );
}

function QualityRow({ name, quality, highlight }: { name: string; quality: Quality; highlight?: boolean }) {
  return (
    <tr className={`border-t border-line/70 ${highlight ? "text-ink" : "text-ink-2"}`}>
      <td className="py-1.5 pr-2">{name}</td>
      <td className="py-1.5 pl-3 text-right tabular-nums">{formatPercent(quality.precision)}</td>
      <td className="py-1.5 pl-3 text-right tabular-nums">{formatPercent(quality.recall)}</td>
      <td className="py-1.5 pl-3 text-right tabular-nums">
        {formatPercent(quality.false_positive_rate, 2)}
      </td>
    </tr>
  );
}

export default function ModelMetrics({ metrics }: { metrics: Metrics | null }) {
  if (!metrics) {
    return (
      <Card title="Model quality">
        <EmptyState>Loading offline evaluation…</EmptyState>
      </Card>
    );
  }
  // Recharts draws left to right, so sort the curve by recall.
  const curve = [...metrics.pr_curve].sort((a, b) => a.recall - b.recall);
  const byType = Object.entries(metrics.recall_by_fraud_type);

  return (
    <Card
      title="Model quality"
      subtitle={`Held-out test period · ${metrics.rows.test.toLocaleString("en-IN")} transactions, ${formatPercent(metrics.fraud_rate, 2)} fraud`}
      actions={
        <div className="flex gap-4 text-xs text-muted">
          <span>
            PR-AUC <span className="font-semibold tabular-nums text-ink">{metrics.pr_auc.toFixed(3)}</span>
          </span>
          <span>
            ROC-AUC <span className="font-semibold tabular-nums text-ink">{metrics.roc_auc.toFixed(3)}</span>
          </span>
        </div>
      }
    >
      <div className="grid flex-1 gap-x-6 gap-y-4 px-4 pb-4 pt-3 lg:grid-cols-2">
        <div>
          <h3 className="text-xs font-medium text-ink-2">Precision-recall curve</h3>
          <p className="text-xs text-muted">
            The dot is the alert threshold in use ({metrics.threshold.toFixed(2)})
          </p>
          <div className="mt-2 h-56">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={curve} margin={{ top: 8, right: 12, bottom: 16, left: -8 }}>
                <CartesianGrid stroke="var(--color-line)" vertical={false} />
                <XAxis
                  dataKey="recall"
                  type="number"
                  domain={[0, 1]}
                  ticks={AXIS_TICKS}
                  tickFormatter={percentTick}
                  tick={tickStyle}
                  stroke="var(--color-line)"
                  label={{ value: "Recall", position: "insideBottom", offset: -10, ...tickStyle }}
                />
                <YAxis
                  type="number"
                  domain={[0, 1]}
                  ticks={AXIS_TICKS}
                  tickFormatter={percentTick}
                  tick={tickStyle}
                  stroke="var(--color-line)"
                  label={{ value: "Precision", angle: -90, position: "insideLeft", offset: 18, ...tickStyle }}
                />
                <Tooltip content={<CurveTooltip />} cursor={{ stroke: "var(--color-muted)", strokeDasharray: "3 3" }} />
                <Line
                  dataKey="precision"
                  type="linear"
                  stroke="var(--color-series-1)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, stroke: "var(--color-surface)", strokeWidth: 2 }}
                  isAnimationActive={false}
                />
                <ReferenceDot
                  x={metrics.model.recall}
                  y={metrics.model.precision}
                  r={5}
                  fill="var(--color-ink)"
                  stroke="var(--color-surface)"
                  strokeWidth={2}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="space-y-5">
          <div>
            <h3 className="text-xs font-medium text-ink-2">Model versus rules</h3>
            <table className="mt-1 w-full text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="py-1 text-left font-normal">Detector</th>
                  <th className="py-1 pl-3 text-right font-normal">Precision</th>
                  <th className="py-1 pl-3 text-right font-normal">Recall</th>
                  <th className="py-1 pl-3 text-right font-normal">False-alarm rate</th>
                </tr>
              </thead>
              <tbody>
                <QualityRow name="XGBoost model" quality={metrics.model} highlight />
                <QualityRow name="Rules only" quality={metrics.rules_only} />
                <QualityRow name="Model or rules (live)" quality={metrics.model_or_rules} />
              </tbody>
            </table>
          </div>

          <div>
            <h3 className="text-xs font-medium text-ink-2">Model recall by fraud pattern</h3>
            <ul className="mt-2 space-y-2">
              {byType.map(([kind, recall]) => (
                <li key={kind} className="grid grid-cols-[9rem_1fr_3rem] items-center gap-2 text-xs">
                  <span className="truncate text-ink-2">{titleCase(kind)}</span>
                  <span className="h-2 overflow-hidden rounded-full bg-raised">
                    <span
                      className="block h-full rounded-full bg-series-1"
                      style={{ width: `${recall * 100}%` }}
                    />
                  </span>
                  <span className="text-right tabular-nums text-ink">{formatPercent(recall, 0)}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </Card>
  );
}
