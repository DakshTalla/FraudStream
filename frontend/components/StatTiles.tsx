import { formatCompact, formatPercent } from "@/lib/api";
import type { Stats } from "@/lib/types";

/** A tiny line chart with no axes: shape of the last minute of throughput. */
function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const max = Math.max(...values, 1);
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * 100},${26 - (v / max) * 24}`)
    .join(" ");
  return (
    <svg viewBox="0 0 100 28" preserveAspectRatio="none" className="h-7 w-full" aria-hidden>
      <polyline
        points={points}
        fill="none"
        stroke="var(--color-series-1)"
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Tile({
  label,
  value,
  unit,
  detail,
  children,
}: {
  label: string;
  value: string;
  unit?: string;
  detail: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">
        {value}
        {unit && <span className="ml-1 text-sm font-normal text-muted">{unit}</span>}
      </p>
      {children}
      <p className="mt-1 truncate text-xs tabular-nums text-muted">{detail}</p>
    </div>
  );
}

function uptime(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return minutes > 0 ? `${minutes}m ${seconds % 60}s` : `${seconds}s`;
}

export default function StatTiles({
  stats,
  tpsHistory,
}: {
  stats: Stats | null;
  tpsHistory: number[];
}) {
  const c = stats?.confusion;
  const latency = stats?.latency_ms;
  const dash = "–";
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Tile
        label="Transactions scored"
        value={stats ? formatCompact(stats.processed) : dash}
        detail={stats ? `Running for ${uptime(stats.uptime_s)}` : "Waiting for stream"}
      />
      <Tile
        label="Throughput"
        value={stats ? stats.throughput_tps.toFixed(1) : dash}
        unit="txn/s"
        detail="Last 60 seconds"
      >
        <Sparkline values={tpsHistory} />
      </Tile>
      <Tile
        label="End-to-end latency (p50)"
        value={latency?.p50 !== undefined ? latency.p50.toFixed(1) : dash}
        unit="ms"
        detail={
          latency?.p95 !== undefined ? `p95 ${latency.p95} ms · p99 ${latency.p99} ms` : "No data"
        }
      />
      <Tile
        label="Alerts raised"
        value={stats ? formatCompact(stats.alerts) : dash}
        detail={stats ? `${stats.review.pending} waiting for review` : "No data"}
      />
      <Tile
        label="Live precision"
        value={stats && stats.alerts > 0 ? formatPercent(stats.live_precision) : dash}
        detail={c ? `${c.tp} true alerts · ${c.fp} false alarms` : "No data"}
      />
      <Tile
        label="Live recall"
        value={c && c.tp + c.fn > 0 ? formatPercent(stats.live_recall) : dash}
        detail={c ? `${c.fn} fraud payments missed` : "No data"}
      />
    </div>
  );
}
