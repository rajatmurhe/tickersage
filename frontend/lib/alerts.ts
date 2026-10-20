// Smart alerts: types mirroring backend/app/alerts (compiler, store, API) and pure helpers.

import { API_URL, ApiError } from "./api";
import { errorDetail } from "./strategy";

export type AskField = "symbol" | "condition" | "interval" | "threshold" | "window";
export const ASK_FIELDS: AskField[] = ["symbol", "condition", "interval", "threshold", "window"];
export const MAX_TEXT = 500;
export const MAX_ANSWER = 200;

export type AlertCondition =
  | { kind: "rsi"; period: number; interval: string; op: "below" | "above"; value: number }
  | { kind: "price_vs_sma"; period: number; interval: string; op: "below" | "above" | "crosses" }
  | { kind: "sma_cross"; fast: number; slow: number; interval: string; op: "above" | "below" }
  | { kind: "price_change"; direction: "up" | "down" | "either"; pct: number; window_hours: number }
  | { kind: "price_level"; op: "above" | "below" | "reaches"; price: number }
  | { kind: "news_sentiment"; tone: "negative" | "positive"; threshold: number | null };

export interface AlertSpec {
  symbol: string;
  conditions: AlertCondition[];
  logic: "all" | "any";
  cooldown_minutes: number;
}

export interface Question {
  field: AskField;
  question: string;
  example: string;
}

export interface CompileResult {
  status: "ok" | "needs_clarification" | "rejected";
  spec: AlertSpec | null;
  questions: Question[];
  summary: string[];
  source: "rules" | "llm";
  fallback_reason: string | null;
  notes: string[];
  dropped: string[];
  rejection: string | null;
  rejection_category: "injection" | "trade" | "advice" | null;
  llm: { model: string; provider: string; latency_ms: number; cost_usd: number; fell_back: boolean } | null;
  text: string;
  disclaimer: string;
}

export type Action = "fire" | "wait_rearm" | "rearm" | "cooldown" | "idle" | "error";

export interface Rule {
  id: string;
  created_at: string;
  text: string;
  spec: AlertSpec;
  summary: string[];
  channels: string[];
  source: string;
  enabled: boolean;
  armed: boolean;
  last_checked_at: string | null;
  last_fired_at: string | null;
  last_action: Action | null;
  last_error: string | null;
  fire_count: number;
}

export interface ConditionResult {
  index: number;
  kind: string;
  description: string;
  met: boolean | null;
  observed: Record<string, unknown>;
  detail: string;
}

export interface AlertEvent {
  id: number;
  rule_id: string;
  created_at: string;
  symbol: string;
  rule_text: string;
  conditions: ConditionResult[];
  brief: string;
  brief_source: "llm" | "template";
  verification: { checked: number; matched: number; unsupported: string[]; fallback_reason: string | null };
  deliveries: Record<string, string>;
  read: boolean;
}

export interface CheckResult {
  rule_id: string;
  action: Action;
  event: AlertEvent | null;
  rule: Rule;
  evaluation: { met: boolean | null; conditions: ConditionResult[]; errors: string[] };
}

export interface Status {
  scheduler: boolean;
  interval_s: number;
  last_run: { at: string; rules: number; actions: Record<string, number> } | null;
  channels: Record<"in_app" | "telegram" | "email", boolean>;
  external_channels_need_admin: boolean;
  unread: number;
  max_rules: number;
  disclaimer: string;
}

export interface WatchItem {
  symbol: string;
  added_at: string;
  rules: number;
}

// ------------------------------------------------------------------ client id

const CLIENT_KEY = "tickersage.client-id";
let memoryId: string | null = null;

/** A random id for this browser, kept in local storage; the API scopes rules and alerts to it. */
export function clientId(): string {
  try {
    const stored = window.localStorage.getItem(CLIENT_KEY);
    if (stored && /^[A-Za-z0-9_-]{8,64}$/.test(stored)) return stored;
    const id = newId();
    window.localStorage.setItem(CLIENT_KEY, id);
    return id;
  } catch {
    // Private mode or blocked storage: one id for this page load.
    memoryId ??= newId();
    return memoryId;
  }
}

export function newId(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return "c-" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

// ------------------------------------------------------------------ API

export async function alertsApi<T>(
  path: string,
  init: { method?: "GET" | "POST" | "DELETE"; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}${path}`, {
      method: init.method ?? (init.body === undefined ? "GET" : "POST"),
      headers: { "X-Client-Id": clientId(), ...(init.body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: init.signal,
      cache: "no-store",
    });
  } catch (err) {
    if (init.signal?.aborted) throw err;
    throw new ApiError(`Could not reach the API at ${API_URL}. Is the backend running?`);
  }
  if (!resp.ok) throw new ApiError(await errorDetail(resp), resp.status);
  return (await resp.json()) as T;
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

// ------------------------------------------------------------------ wording

export const ACTION_LABELS: Record<Action, string> = {
  fire: "fired",
  wait_rearm: "still true: waiting to re-arm",
  rearm: "re-armed",
  cooldown: "true, but in cooldown",
  idle: "not met",
  error: "data unavailable",
};

/** "just now", "5 min ago", "3 h ago", "2 days ago" (relative to `now`). */
export function timeAgo(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "never";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "unknown";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h} h ago`;
  const d = Math.round(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

/** 60 -> "1 hour", 1440 -> "1 day", 90 -> "90 min". */
export function cooldownLabel(minutes: number): string {
  if (minutes % 1440 === 0) return `${minutes / 1440} day${minutes === 1440 ? "" : "s"}`;
  if (minutes % 60 === 0) return `${minutes / 60} hour${minutes === 60 ? "" : "s"}`;
  return `${minutes} min`;
}

/** Delivery results worth showing: anything other than the always-on in-app one. */
export function deliveryNotes(deliveries: Record<string, string>): string[] {
  return Object.entries(deliveries)
    .filter(([channel]) => channel !== "in_app")
    .map(([channel, outcome]) => `${channel === "telegram" ? "Telegram" : "Email"}: ${outcome}`);
}
