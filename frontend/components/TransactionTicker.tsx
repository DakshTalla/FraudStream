import { formatClock, formatRupees, shortId } from "@/lib/api";
import type { TickerRow } from "@/lib/types";
import Card, { EmptyState } from "./Card";

/** Model score as a number plus a small bar. Flagged rows get the alert colour
 *  AND the word "Alert", so colour is never the only signal. */
function Risk({ score, flagged }: { score: number; flagged: boolean }) {
  return (
    <div className="flex items-center justify-end gap-2">
      {flagged && (
        <span className="rounded border border-critical/60 bg-critical/15 px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-ink">
          Alert
        </span>
      )}
      <span className="w-9 text-right tabular-nums text-ink-2">{score.toFixed(2)}</span>
      <span className="h-1.5 w-10 overflow-hidden rounded-full bg-raised">
        <span
          className={`block h-full rounded-full ${flagged ? "bg-critical" : "bg-muted"}`}
          style={{ width: `${Math.max(score * 100, 4)}%` }}
        />
      </span>
    </div>
  );
}

export default function TransactionTicker({ rows }: { rows: TickerRow[] }) {
  return (
    <Card title="Live transactions" subtitle="Event time (IST) · newest first" className="h-[34rem]">
      {rows.length === 0 ? (
        <EmptyState>Waiting for transactions…</EmptyState>
      ) : (
        <div className="scroll-quiet flex-1 overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface text-left text-muted">
              <tr>
                <th className="px-4 py-2 font-normal">Time</th>
                <th className="px-2 py-2 font-normal">Payer → payee</th>
                <th className="px-2 py-2 text-right font-normal">Amount</th>
                <th className="px-4 py-2 text-right font-normal">Risk score</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ txn, score, is_alert }) => (
                <tr
                  key={txn.txn_id}
                  className={`row-in border-t border-line/60 ${is_alert ? "bg-critical/10" : ""}`}
                >
                  <td className="whitespace-nowrap px-4 py-1.5 tabular-nums text-muted">
                    {formatClock(txn.ts)}
                  </td>
                  <td className="max-w-0 w-full truncate px-2 py-1.5 font-mono text-ink-2">
                    {shortId(txn.payer)} <span className="text-muted">→</span> {shortId(txn.payee)}
                    <span className="ml-2 font-sans text-muted">{txn.city}</span>
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums text-ink">
                    {formatRupees(txn.amount)}
                  </td>
                  <td className="whitespace-nowrap px-4 py-1.5">
                    <Risk score={score} flagged={is_alert} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
