"use client";

import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationNodeDatum,
} from "d3-force";
import { useMemo, useState } from "react";
import { formatRupees, shortId } from "@/lib/api";
import type { Ring, RingNode } from "@/lib/types";
import Card, { EmptyState } from "./Card";

const WIDTH = 640;
const HEIGHT = 360;
const PADDING = 44;

// Role -> colour. Three validated hues; the leftover role is neutral grey.
// Mules and collectors are also larger and labelled, so colour is not the
// only way to tell roles apart.
const ROLE_STYLE: Record<RingNode["role"], { color: string; label: string }> = {
  sender: { color: "var(--color-series-1)", label: "Sender (likely victim)" },
  mule: { color: "var(--color-series-2)", label: "Mule (receives and forwards)" },
  collector: { color: "var(--color-series-3)", label: "Collector (money ends here)" },
  receiver: { color: "var(--color-muted)", label: "Other receiver" },
};

type PlacedNode = RingNode & { x: number; y: number; radius: number };

/**
 * Compute node positions with a force simulation: connected nodes pull
 * together, all nodes push apart. We run it to completion up front (no
 * animation) and then rescale the result to fill the drawing area.
 */
function layout(ring: Ring): PlacedNode[] {
  const nodes = ring.nodes.map((n) => ({ ...n }) as RingNode & SimulationNodeDatum);
  const links = ring.edges.map((e) => ({ source: e.source, target: e.target }));
  forceSimulation(nodes)
    .force("link", forceLink(links).id((d) => (d as RingNode).id).distance(60))
    .force("charge", forceManyBody().strength(-180))
    .force("center", forceCenter(0, 0))
    .force("collide", forceCollide(26))
    .stop()
    .tick(300);

  const xs = nodes.map((n) => n.x ?? 0);
  const ys = nodes.map((n) => n.y ?? 0);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const scale = Math.min(
    (WIDTH - 2 * PADDING) / Math.max(maxX - minX, 1),
    (HEIGHT - 2 * PADDING) / Math.max(maxY - minY, 1),
    4, // do not blow a tiny ring up to fill the whole panel
  );
  return nodes.map((n) => ({
    id: n.id,
    role: n.role,
    in_degree: n.in_degree,
    out_degree: n.out_degree,
    x: WIDTH / 2 + ((n.x ?? 0) - (minX + maxX) / 2) * scale,
    y: HEIGHT / 2 + ((n.y ?? 0) - (minY + maxY) / 2) * scale,
    radius: n.role === "sender" || n.role === "receiver" ? 7 : 11,
  }));
}

export default function MuleGraph({ rings }: { rings: Ring[] }) {
  const [selected, setSelected] = useState(0);
  const [hovered, setHovered] = useState<PlacedNode | null>(null);
  const ring = rings[Math.min(selected, rings.length - 1)];

  // Recompute the layout only when the ring's shape actually changes, so the
  // picture does not jump on every poll.
  const signature = ring ? ring.edges.map((e) => `${e.source}>${e.target}`).join("|") : "";
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const nodes = useMemo(() => (ring ? layout(ring) : []), [signature]);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const maxAmount = ring ? Math.max(...ring.edges.map((e) => e.amount), 1) : 1;
  const rolesPresent = (Object.keys(ROLE_STYLE) as RingNode["role"][]).filter((role) =>
    nodes.some((n) => n.role === role),
  );

  return (
    <Card
      title="Suspected mule rings"
      subtitle="Accounts linked by alerted payments · arrows show where money moved"
      actions={
        rings.length > 0 && (
          <div className="flex flex-wrap gap-1 text-xs" role="tablist">
            {rings.map((r, i) => (
              <button
                key={i}
                role="tab"
                aria-selected={ring === r}
                onClick={() => setSelected(i)}
                className={`rounded-md border px-2 py-1 tabular-nums transition-colors ${
                  ring === r
                    ? "border-line bg-raised text-ink"
                    : "border-transparent text-muted hover:text-ink-2"
                }`}
              >
                Ring {i + 1} · {r.nodes.length} accounts
              </button>
            ))}
          </div>
        )
      }
    >
      {!ring ? (
        <EmptyState>
          No rings detected yet. A ring appears once several alerted payments connect the same
          accounts.
        </EmptyState>
      ) : (
        <div className="flex flex-1 flex-col px-4 pb-4 pt-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
            {rolesPresent.map((role) => (
              <span key={role} className="inline-flex items-center gap-1.5">
                <span
                  className="h-2.5 w-2.5 rounded-full"
                  style={{ background: ROLE_STYLE[role].color }}
                />
                {ROLE_STYLE[role].label}
              </span>
            ))}
            <span className="ml-auto tabular-nums text-muted">
              {formatRupees(ring.total_amount)} moved in {ring.edges.length} links
            </span>
          </div>

          <div className="relative mt-2">
            <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" role="img"
              aria-label={`Payment graph of ${ring.nodes.length} linked accounts`}>
              <defs>
                {/* userSpaceOnUse: arrowheads keep one size on thick and thin lines */}
                <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8"
                  markerHeight="8" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" fill="var(--color-muted)" />
                </marker>
              </defs>
              {ring.edges.map((edge) => {
                const from = byId.get(edge.source);
                const to = byId.get(edge.target);
                if (!from || !to) return null;
                // Stop the line at the edge of the target circle so the
                // arrowhead stays visible.
                const length = Math.hypot(to.x - from.x, to.y - from.y) || 1;
                const ux = (to.x - from.x) / length;
                const uy = (to.y - from.y) / length;
                return (
                  <line
                    key={`${edge.source}>${edge.target}`}
                    x1={from.x + ux * from.radius}
                    y1={from.y + uy * from.radius}
                    x2={to.x - ux * (to.radius + 4)}
                    y2={to.y - uy * (to.radius + 4)}
                    stroke="var(--color-muted)"
                    strokeOpacity={0.7}
                    strokeWidth={1 + (edge.amount / maxAmount) * 2.5}
                    markerEnd="url(#arrow)"
                  />
                );
              })}
              {nodes.map((node) => (
                <g key={node.id}>
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={node.radius}
                    fill={ROLE_STYLE[node.role].color}
                    stroke="var(--color-surface)"
                    strokeWidth={2}
                  />
                  {(node.role === "mule" || node.role === "collector") && (
                    <text x={node.x} y={node.y + node.radius + 13} textAnchor="middle"
                      fontSize="11" fill="var(--color-ink-2)" className="font-mono"
                      stroke="var(--color-surface)" strokeWidth={3} paintOrder="stroke">
                      {shortId(node.id)}
                    </text>
                  )}
                  {/* Invisible, larger hover target: easier to hit than the dot. */}
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={node.radius + 8}
                    fill="transparent"
                    onMouseEnter={() => setHovered(node)}
                    onMouseLeave={() => setHovered(null)}
                  />
                </g>
              ))}
            </svg>
            {hovered && (
              <div
                className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md border border-line bg-raised px-2.5 py-1.5 text-xs shadow-lg"
                style={{
                  left: `${(hovered.x / WIDTH) * 100}%`,
                  top: `calc(${(hovered.y / HEIGHT) * 100}% - 14px)`,
                }}
              >
                <p className="font-mono text-ink">{shortId(hovered.id)}</p>
                <p className="whitespace-nowrap text-muted">
                  {ROLE_STYLE[hovered.role].label.split(" (")[0]} · {hovered.in_degree} in ·{" "}
                  {hovered.out_degree} out
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
