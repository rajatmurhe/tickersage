import { API_URL, ApiError, failureMessage, parseEvent } from "./api";
import type { Citation, Status, StepEvent, Trace } from "./types";

// Mirrors backend/app/report/models.py.

export interface SectionCheck {
  mode: "llm" | "template";
  attempts: number;
  checked: number;
  matched: number;
  unsupported: string[];
  fell_back: boolean;
  removed: string[];
}

export interface AssetSection {
  asset: string;
  name: string;
  markdown: string;
  check: SectionCheck;
  tools: { tool: string; ok: boolean; error: string | null; ms: number }[];
}

export interface PriceSeries {
  asset: string;
  interval: string;
  points: { t: string; close: number; index: number }[];
  cite: number | null;
}

export interface VolatilityBar {
  asset: string;
  volatility_pct: number;
  annualized_volatility_pct: number;
  interval: string;
  cite: number | null;
}

export interface Report {
  request_id: string;
  question: string;
  status: Status;
  title: string;
  window: "24h" | "7d" | "30d" | null;
  assets: string[];
  message: string | null;
  summary: string;
  sections: AssetSection[];
  comparison: string | null;
  risks: string[];
  citations: Citation[];
  charts: { price: PriceSeries[]; volatility: VolatilityBar[] };
  markdown: string;
  writer: SectionCheck | null;
  verification: { checked: number; matched: number; unsupported: string[] } | null;
  policy_denied: { tool: string; args: Record<string, unknown>; reason: string }[];
  notes: string[];
  data_as_of: string | null;
  trace: Trace;
  html: string | null;
}

export interface ReportHandlers {
  onStep: (step: StepEvent) => void;
  onDone: (report: Report) => void;
}

/** POST /api/report and dispatch its SSE events: a `step` per finished node (one per asset sub-agent), then `done`. */
export async function streamReport(question: string, handlers: ReportHandlers, signal?: AbortSignal): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}/api/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ question }),
      signal,
    });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError(`Could not reach the API at ${API_URL}. Is the backend running?`);
  }
  if (!resp.ok || !resp.body) throw new ApiError(await failureMessage(resp), resp.status);

  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  let finished = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let boundary: RegExpExecArray | null;
    while ((boundary = /\r\n\r\n|\n\n|\r\r/.exec(buf))) {
      const raw = buf.slice(0, boundary.index);
      buf = buf.slice(boundary.index + boundary[0].length);
      const event = parseEvent(raw);
      if (!event) continue;
      if (event.type === "step") handlers.onStep(event.data as StepEvent);
      else if (event.type === "done") {
        finished = true;
        handlers.onDone(event.data as Report);
      }
    }
  }
  if (!finished) throw new ApiError("The report stream ended early. Please try again.");
}

// ---------------------------------------------------------------- charts

export const CHART = { w: 640, h: 240, left: 44, right: 12, top: 12, bottom: 30 };

export interface Scale {
  lo: number;
  hi: number;
  ticks: number[];
}

/** Y range over every series' rebased values, padded so lines don't touch the frame, with 5 even ticks. */
export function priceScale(series: PriceSeries[]): Scale | null {
  const values = series.flatMap((s) => s.points.map((p) => p.index));
  if (values.length < 2) return null;
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  const pad = Math.max((hi - lo) * 0.08, 0.5);
  lo -= pad;
  hi += pad;
  return { lo, hi, ticks: [0, 1, 2, 3, 4].map((i) => lo + ((hi - lo) * i) / 4) };
}

/** SVG polyline points for one series inside the chart frame. */
export function polyline(series: PriceSeries, scale: Scale, c = CHART): string {
  const pw = c.w - c.left - c.right;
  const ph = c.h - c.top - c.bottom;
  const n = series.points.length;
  return series.points
    .map((p, i) => {
      const x = c.left + (pw * i) / Math.max(n - 1, 1);
      const y = c.top + ph * (1 - (p.index - scale.lo) / (scale.hi - scale.lo));
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/** Download a string as a file in the browser. */
export function downloadText(text: string, filename: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function slug(title: string): string {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "report";
}
