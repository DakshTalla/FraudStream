"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, sendFeedback, WS_URL } from "./api";
import type {
  Alert,
  AlertStatus,
  Health,
  Metrics,
  Ring,
  ServerMessage,
  Stats,
  TickerRow,
} from "./types";

const TICKER_ROWS = 40;
const MAX_ALERTS = 200;
const HISTORY_POINTS = 60;

/** Insert or replace an alert by id, newest first. */
function upsert(alerts: Alert[], alert: Alert): Alert[] {
  const exists = alerts.some((a) => a.id === alert.id);
  const next = exists ? alerts.map((a) => (a.id === alert.id ? alert : a)) : [alert, ...alerts];
  return next.slice(0, MAX_ALERTS);
}

/**
 * All live data for the dashboard in one hook.
 *
 * - WebSocket: transactions, new alerts, alert updates and stats are PUSHED.
 * - REST: a snapshot (alerts, metrics) is fetched once per connection, and the
 *   ring graph is polled every few seconds because it changes slowly.
 */
export function useFraudStream() {
  const [connected, setConnected] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [tpsHistory, setTpsHistory] = useState<number[]>([]);
  const [ticker, setTicker] = useState<TickerRow[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [rings, setRings] = useState<Ring[]>([]);

  // WHY a buffer: transactions arrive many times per second. Re-rendering on
  // every message wastes CPU, so we collect them and update the screen ~3x/sec.
  const buffer = useRef<TickerRow[]>([]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const loadSnapshot = () => {
      getJSON<Health>("/api/health").then(setHealth).catch(() => {});
      getJSON<Metrics>("/api/metrics").then(setMetrics).catch(() => {});
      getJSON<Alert[]>(`/api/alerts?limit=${MAX_ALERTS}`).then(setAlerts).catch(() => {});
    };

    const connect = () => {
      socket = new WebSocket(WS_URL);
      socket.onopen = () => {
        setConnected(true);
        loadSnapshot();
      };
      socket.onclose = () => {
        setConnected(false);
        // Auto-reconnect so the dashboard recovers if the backend restarts.
        if (!stopped) retry = setTimeout(connect, 2000);
      };
      socket.onmessage = (event) => {
        const message = JSON.parse(event.data) as ServerMessage;
        if (message.type === "txn") {
          buffer.current.push(message);
        } else if (message.type === "stats") {
          setStats(message.stats);
          setTpsHistory((h) => [...h, message.stats.throughput_tps].slice(-HISTORY_POINTS));
        } else {
          setAlerts((current) => upsert(current, message.alert));
        }
      };
    };
    connect();

    const flush = setInterval(() => {
      if (buffer.current.length === 0) return;
      const fresh = buffer.current.reverse(); // newest first
      buffer.current = [];
      setTicker((rows) => [...fresh, ...rows].slice(0, TICKER_ROWS));
    }, 300);

    const pollRings = setInterval(() => {
      getJSON<Ring[]>("/api/graph").then(setRings).catch(() => {});
    }, 4000);

    return () => {
      stopped = true;
      clearTimeout(retry);
      clearInterval(flush);
      clearInterval(pollRings);
      socket?.close();
    };
  }, []);

  /** Analyst decision. The server also broadcasts the update to other tabs. */
  const review = useCallback(async (id: number, decision: Exclude<AlertStatus, "pending">) => {
    const updated = await sendFeedback(id, decision);
    setAlerts((current) => upsert(current, updated));
  }, []);

  return { connected, health, metrics, stats, tpsHistory, ticker, alerts, rings, review };
}
