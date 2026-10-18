import { STATUS_PILL } from "./format";
import type { Status, StepTrace, ToolTrace, Trace } from "./types";

/** One row of GET /api/traces (backend/app/tracing.py). Columns can be null on old or partial rows. */
export interface TraceRow {
  request_id: string;
  created_at: string | null;
  question: string | null;
  intent: string | null;
  status: string | null;
  refusal_reason: string | null;
  tools: ToolTrace[] | null;
  steps: StepTrace[] | null;
  models: string[] | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  fell_back: boolean | null;
  faithfulness: number | null;
  unsupported_numbers: string[] | null;
}

/** A stored row adapted to the Trace shape TracePanel renders, plus what was asked and when. */
export interface StoredTrace extends Trace {
  question: string;
  createdAt: Date | null;
}

/** Statuses the summary always lists, in display order. Others appear only when present. */
export const STATUS_ORDER: Status[] = ["ok", "refused", "out_of_scope", "safe_fallback", "error"];

export function statusPill(status: string): { label: string; tone: "" | "warn" | "bad" } {
  return STATUS_PILL[status as Status] ?? { label: status.replace(/_/g, " "), tone: "bad" };
}

/**
 * The API serialises datetimes with str(): SQLite gives naive UTC ("2026-10-18 17:56:48.504488"),
 * PostgreSQL appends "+00:00". A timestamp without an offset is UTC.
 */
export function parseCreatedAt(value: string | null | undefined): Date | null {
  if (!value) return null;
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(\.\d+)?(Z|[+-]\d{2}(?::?\d{2})?)?$/i.exec(value.trim());
  let iso = value;
  if (m) {
    const [, date, time, frac = "", zone = "Z"] = m;
    const offset = /^[+-]\d{4}$/.test(zone) ? `${zone.slice(0, 3)}:${zone.slice(3)}` : zone.length === 3 ? `${zone}:00` : zone;
    iso = `${date}T${time}${frac.slice(0, 4)}${offset.toUpperCase()}`;
  }
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

const num = (x: unknown) => (typeof x === "number" && Number.isFinite(x) ? x : 0);
const list = <T,>(x: T[] | null | undefined): T[] => (Array.isArray(x) ? x : []);

export function toStoredTrace(r: TraceRow): StoredTrace {
  return {
    request_id: String(r.request_id),
    question: r.question ?? "",
    createdAt: parseCreatedAt(r.created_at),
    intent: r.intent ?? null,
    status: (r.status ?? "error") as Status,
    refusal_reason: r.refusal_reason ?? null,
    tools: list(r.tools).map((t, i) => ({ ...t, id: t.id ?? i + 1, args: t.args ?? {} })),
    steps: list(r.steps).map((s) => ({ ...s, latency_ms: num(s.latency_ms), detail: s.detail ?? {} })),
    models: list(r.models),
    input_tokens: num(r.input_tokens),
    output_tokens: num(r.output_tokens),
    cost_usd: num(r.cost_usd),
    latency_ms: num(r.latency_ms),
    fell_back: Boolean(r.fell_back),
    faithfulness: typeof r.faithfulness === "number" ? r.faithfulness : null,
    unsupported_numbers: list(r.unsupported_numbers),
  };
}

/** Nearest-rank percentile on the rounded index, the same rule as evals/scoring.py `_pct`. */
export function percentile(values: number[], q: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))));
  return sorted[idx];
}

export interface TraceSummary {
  count: number;
  p50: number;
  p95: number;
  totalCost: number;
  avgCost: number;
  /** Status → request count. Every STATUS_ORDER key is present, others only when seen. */
  statusCounts: Record<string, number>;
}

export function summarize(rows: StoredTrace[]): TraceSummary {
  const latencies = rows.map((r) => r.latency_ms);
  const totalCost = rows.reduce((sum, r) => sum + r.cost_usd, 0);
  const statusCounts: Record<string, number> = Object.fromEntries(STATUS_ORDER.map((s) => [s, 0]));
  for (const r of rows) statusCounts[r.status] = (statusCounts[r.status] ?? 0) + 1;
  return {
    count: rows.length,
    p50: percentile(latencies, 0.5),
    p95: percentile(latencies, 0.95),
    totalCost,
    avgCost: rows.length ? totalCost / rows.length : 0,
    statusCounts,
  };
}

/** Distinct tool names in call order, e.g. ["get_ticker", "get_klines"]. */
export function toolNames(t: Trace): string[] {
  return [...new Set(t.tools.map((x) => x.tool))];
}
