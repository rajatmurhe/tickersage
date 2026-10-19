// Strategy Studio: types mirroring backend/app/strategy (compiler, linter, backtest) and pure helpers.

import { API_URL, ApiError } from "./api";

export type AskField = "symbol" | "interval" | "entry" | "lookback" | "stop_loss" | "exit" | "position_size";
export const ASK_FIELDS: AskField[] = ["symbol", "interval", "entry", "lookback", "stop_loss", "exit", "position_size"];
export const MAX_ANSWER = 200;

export type Condition =
  | { kind: "rsi"; period: number; op: string; value: number }
  | { kind: "price_vs_sma"; period: number; op: string }
  | { kind: "sma_cross"; fast: number; slow: number; op: string }
  | { kind: "price_change"; direction: "down" | "up"; pct: number; lookback_hours: number };

export interface StrategySpec {
  symbol: string;
  interval: "15m" | "1h" | "4h" | "1d";
  entry: Condition[];
  entry_logic: "all" | "any";
  exit_signals: Condition[];
  take_profit_pct: number | null;
  stop_loss_pct: number | null;
  max_hold_hours: number | null;
  position_size_pct: number;
  demo_balance: number;
  fee_pct: number;
  slippage_pct: number;
}

export interface Finding {
  rule: string;
  severity: "error" | "warn";
  field: string;
  message: string;
}

export interface Question {
  field: AskField;
  question: string;
  example: string;
}

export interface CompileResult {
  status: "ok" | "needs_clarification" | "rejected";
  spec: StrategySpec | null;
  questions: Question[];
  findings: Finding[];
  summary: string[];
  mermaid: string | null;
  source: "rules" | "llm";
  fallback_reason: string | null;
  notes: string[];
  rejection: string | null;
  rejection_category: "injection" | "execution" | null;
  llm: { model: string; provider: string; latency_ms: number; cost_usd: number; fell_back: boolean } | null;
  lint_text: string;
  disclaimer: string;
}

export interface Trade {
  entry_time: string;
  entry_price: number;
  exit_time: string;
  exit_price: number;
  exit_reason: "take_profit" | "stop_loss" | "exit_signal" | "time_exit" | "end_of_data";
  bars_held: number;
  notional: number;
  pnl: number;
  return_pct: number;
}

export interface Metrics {
  initial_balance: number;
  final_balance: number;
  total_return_pct: number;
  max_drawdown_pct: number;
  trade_count: number;
  win_rate_pct: number | null;
  avg_trade_pct: number | null;
  best_trade_pct: number | null;
  worst_trade_pct: number | null;
  exposure_pct: number;
  fees_paid: number;
  buy_and_hold_return_pct: number;
}

export interface BacktestResult {
  symbol: string;
  interval: string;
  source: string | null;
  as_of: string | null;
  start: string;
  end: string;
  candles: number;
  warmup_candles: number;
  metrics: Metrics;
  trades: Trade[];
  equity_curve: { time: string; equity: number }[];
  notes: string[];
}

export interface BacktestResponse {
  status: "ok" | "blocked" | "error";
  findings: Finding[];
  summary: string[];
  result: BacktestResult | null;
  error: string | null;
  disclaimer: string;
}

// ------------------------------------------------------------------ API

/** POST JSON. FastAPI errors carry `detail`: a string, or a list of validation errors on a 422. */
export async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
      cache: "no-store",
    });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError(`Could not reach the API at ${API_URL}. Is the backend running?`);
  }
  if (!resp.ok) throw new ApiError(await errorDetail(resp), resp.status);
  return (await resp.json()) as T;
}

export async function errorDetail(resp: Response): Promise<string> {
  try {
    const body = (await resp.json()) as { detail?: unknown };
    const d = body.detail;
    if (typeof d === "string" && d.trim()) return d;
    if (Array.isArray(d) && d.length) {
      const first = d[0] as { loc?: unknown[]; msg?: unknown };
      const loc = Array.isArray(first.loc) ? first.loc.filter((p) => p !== "body").join(".") : "";
      const msg = typeof first.msg === "string" ? first.msg : "invalid input";
      return loc ? `${loc}: ${msg}` : msg;
    }
  } catch {
    // not JSON
  }
  return `Request failed (${resp.status}).`;
}

/** The answers worth sending: known fields only, trimmed, non-empty, capped at the API's length limit. */
export function cleanAnswers(answers: Record<string, string>): Partial<Record<AskField, string>> {
  const out: Partial<Record<AskField, string>> = {};
  for (const field of ASK_FIELDS) {
    const v = (answers[field] ?? "").trim().slice(0, MAX_ANSWER);
    if (v) out[field] = v;
  }
  return out;
}

export const hasErrors = (findings: Finding[]) => findings.some((f) => f.severity === "error");

// ------------------------------------------------------------------ the rule diagram

export type NodeShape = "box" | "decision" | "terminal";

export interface FlowNode {
  id: string;
  shape: NodeShape;
  lines: string[];
}

export interface FlowEdge {
  from: string;
  to: string;
  label: string | null;
}

export interface Flow {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

const ENTITIES: Record<string, string> = {
  quot: '"', amp: "&", lt: "<", gt: ">", "35": "#", "124": "|", "91": "[", "93": "]", "123": "{", "125": "}",
};

/** Undo Mermaid's #name; / #code; escapes. The result is plain text for React to render. */
export function unescapeMermaid(s: string): string {
  return s.replace(/#(\w+);/g, (m, name: string) => ENTITIES[name] ?? m);
}

const NODE = /^\s*([A-Za-z][\w-]*)(\["(.*)"\]|\{"(.*)"\}|\(\["(.*)"\]\))\s*$/;
const EDGE = /^\s*([A-Za-z][\w-]*)\s*-->\s*(?:\|([^|]*)\|\s*)?([A-Za-z][\w-]*)\s*$/;

/**
 * Read the canonical flowchart the backend writes (backend/app/strategy/diagram.py): one node or one
 * edge per line. Anything else is ignored, so the page draws its own view without a Mermaid runtime.
 */
export function parseFlow(src: string): Flow {
  const nodes: FlowNode[] = [];
  const edges: FlowEdge[] = [];
  for (const line of src.split(/\r?\n/)) {
    const n = NODE.exec(line);
    if (n) {
      const [, id, , box, decision, terminal] = n;
      const raw = box ?? decision ?? terminal ?? "";
      const shape: NodeShape = box !== undefined ? "box" : decision !== undefined ? "decision" : "terminal";
      nodes.push({ id, shape, lines: raw.split(/<br\s*\/?>/i).map(unescapeMermaid) });
      continue;
    }
    const e = EDGE.exec(line);
    if (e) edges.push({ from: e[1], to: e[3], label: e[2] !== undefined ? unescapeMermaid(e[2].trim()) : null });
  }
  return { nodes, edges };
}

export interface FlowRow {
  nodes: { node: FlowNode; via: string | null }[]; // via: the label on the edge into this node, if any
  loops: { label: string | null; to: FlowNode }[]; // edges back to an earlier row
}

/**
 * Lay the flow out top to bottom: nodes in declaration order, one per row, except that consecutive
 * terminal nodes (the exits) share a row. Edges into a node become its "via" label; edges to an
 * earlier row are listed as loops under the row they leave from.
 */
export function flowRows(flow: Flow): FlowRow[] {
  const rows: FlowNode[][] = [];
  for (const node of flow.nodes) {
    const last = rows[rows.length - 1];
    if (last && node.shape === "terminal" && last[0].shape === "terminal") last.push(node);
    else rows.push([node]);
  }
  const rowOf = new Map<string, number>();
  rows.forEach((r, i) => r.forEach((n) => rowOf.set(n.id, i)));
  const byId = new Map(flow.nodes.map((n) => [n.id, n]));
  return rows.map((row, i) => ({
    nodes: row.map((node) => {
      const into = flow.edges.find((e) => e.to === node.id && rowOf.get(e.from) === i - 1);
      return { node, via: into?.label ?? null };
    }),
    loops: flow.edges
      .filter((e) => rowOf.get(e.from) === i && (rowOf.get(e.to) ?? Infinity) < i && byId.has(e.to))
      .filter((e, k, all) => all.findIndex((x) => x.to === e.to && x.label === e.label) === k)
      .map((e) => ({ label: e.label, to: byId.get(e.to)! })),
  }));
}

// ------------------------------------------------------------------ the equity sparkline

export interface Spark {
  path: string;
  baseline: number | null; // y of the reference value, when it is inside the range
  xs: number[];
}

/** An SVG path through `values` scaled into width x height (pad keeps the 2px stroke inside the box). */
export function sparkline(values: number[], width: number, height: number, reference?: number, pad = 2): Spark {
  if (!values.length) return { path: "", baseline: null, xs: [] };
  const lo = Math.min(...values, reference ?? Infinity);
  const hi = Math.max(...values, reference ?? -Infinity);
  const span = hi - lo;
  const y = (v: number) => (span === 0 ? height / 2 : pad + (1 - (v - lo) / span) * (height - 2 * pad));
  const step = values.length > 1 ? (width - 2 * pad) / (values.length - 1) : 0;
  const xs = values.map((_, i) => (values.length > 1 ? pad + i * step : width / 2));
  const path = values.map((v, i) => `${i ? "L" : "M"}${round1(xs[i])},${round1(y(v))}`).join(" ");
  return { path, baseline: reference === undefined ? null : round1(y(reference)), xs };
}

const round1 = (x: number) => Math.round(x * 10) / 10;

/** Index of the point nearest to x (the crosshair snaps to it). */
export function nearestIndex(xs: number[], x: number): number {
  let best = 0;
  for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - x) < Math.abs(xs[best] - x)) best = i;
  return best;
}

// ------------------------------------------------------------------ formatting

export const signedPct = (x: number | null | undefined) =>
  x == null ? "n/a" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(2)}%`;

export const usd = (x: number) =>
  `${x < 0 ? "−" : ""}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const EXIT_LABELS: Record<Trade["exit_reason"], string> = {
  take_profit: "take profit",
  stop_loss: "stop loss",
  exit_signal: "exit signal",
  time_exit: "time limit",
  end_of_data: "end of data",
};

/** "2026-10-07T02:00:00Z" -> "2026-10-07 02:00" (UTC, as the candles are). */
export const utc = (iso: string) => iso.replace("T", " ").replace(/:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/, "");
