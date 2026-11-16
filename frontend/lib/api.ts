import type { Alert, AlertStatus } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export const WS_URL = API_URL.replace(/^http/, "ws") + "/ws";

export async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(API_URL + path);
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json() as Promise<T>;
}

export async function sendFeedback(id: number, decision: Exclude<AlertStatus, "pending">) {
  const response = await fetch(`${API_URL}/api/alerts/${id}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  });
  if (!response.ok) throw new Error(`feedback returned ${response.status}`);
  return response.json() as Promise<Alert>;
}

// ---- formatting helpers -----------------------------------------------------
const rupees = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});
const compact = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });
const clock = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});
const day = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata",
  day: "numeric",
  month: "short",
});

export const formatRupees = (amount: number) => rupees.format(amount);
export const formatCompact = (n: number) => compact.format(n);
export const formatClock = (ts: number) => clock.format(ts * 1000);
export const formatDay = (ts: number) => day.format(ts * 1000);
export const formatPercent = (fraction: number, digits = 1) =>
  `${(fraction * 100).toFixed(digits)}%`;
export const shortId = (vpa: string) => vpa.replace("@upi", "");
export const titleCase = (snake: string) =>
  snake.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
