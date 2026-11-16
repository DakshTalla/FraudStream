"use client";

import { useFraudStream } from "@/lib/useFraudStream";
import AlertQueue from "./AlertQueue";
import ModelMetrics from "./ModelMetrics";
import MuleGraph from "./MuleGraph";
import StatTiles from "./StatTiles";
import TransactionTicker from "./TransactionTicker";

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs text-ink-2">
      {children}
    </span>
  );
}

export default function Dashboard() {
  const stream = useFraudStream();

  return (
    <main className="mx-auto w-full max-w-[1500px] space-y-3 px-4 py-4 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3 pb-1">
        <div className="flex items-center gap-3">
          <svg viewBox="0 0 24 24" className="h-8 w-8" aria-hidden>
            <path
              d="M12 2 4 5v6c0 5 3.4 9.4 8 11 4.6-1.6 8-6 8-11V5l-8-3Z"
              fill="var(--color-series-1)"
            />
            <path d="M7 12.5h3l1.5-3.5 2 6 1.5-2.5h2" fill="none" stroke="var(--color-page)"
              strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <div>
            <h1 className="text-lg font-semibold leading-tight text-ink">FraudStream</h1>
            <p className="text-xs text-muted">Real-time UPI fraud detection · analyst console</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {stream.health && (
            <>
              <Chip>Broker: {stream.health.broker}</Chip>
              <Chip>Simulating {stream.health.tps_target} txn/s</Chip>
            </>
          )}
          <Chip>
            <span
              className={`h-2 w-2 rounded-full ${stream.connected ? "bg-good" : "bg-warning"}`}
            />
            {stream.connected ? "Live" : "Reconnecting…"}
          </Chip>
        </div>
      </header>

      <StatTiles stats={stream.stats} tpsHistory={stream.tpsHistory} />

      <div className="grid gap-3 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <TransactionTicker rows={stream.ticker} />
        </div>
        <div className="lg:col-span-7">
          <AlertQueue alerts={stream.alerts} onReview={stream.review} />
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-12">
        <div className="flex lg:col-span-5">
          <MuleGraph rings={stream.rings} />
        </div>
        <div className="flex lg:col-span-7">
          <ModelMetrics metrics={stream.metrics} />
        </div>
      </div>
    </main>
  );
}
