"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { STATUS_PILL, ms, safeUrl } from "@/lib/format";
import {
  CHART, downloadText, polyline, priceScale, slug, streamReport,
  type PriceSeries, type Report, type SectionCheck, type VolatilityBar,
} from "@/lib/report";
import type { StepEvent } from "@/lib/types";
import Markdown from "./Markdown";
import TracePanel from "./TracePanel";

const EXAMPLES = [
  "Weekly report on BTC, ETH and SOL",
  "Monthly report on Bitcoin and Ethereum",
  "Daily report on Solana",
  "Weekly report on BTC, ETH and SOL, and tell me which one to buy",
];

const COLORS = ["#2563eb", "#d97706", "#059669", "#db2777", "#7c3aed"];

export default function ReportView() {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState<StepEvent[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => abort.current?.abort(), []);

  async function run(question: string) {
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setBusy(true);
    setSteps([]);
    setReport(null);
    setError(null);
    try {
      await streamReport(
        question,
        { onStep: (s) => setSteps((xs) => [...xs, s]), onDone: setReport },
        controller.signal,
      );
    } catch (err) {
      if (controller.signal.aborted) return;
      setError(err instanceof ApiError ? err.message : "Something went wrong writing the report.");
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const q = input.trim();
    if (q && !busy) void run(q);
  }

  return (
    <div className="report-page">
      <p className="intro">
        Ask for a research report on up to five assets. A planner splits it into one task per asset, sub-agents gather
        each asset&apos;s data in parallel and write its section, and an editor writes the summary and risks. Every number is
        checked against the tool data before you see it. Research only: no trading, no advice.
      </p>
      <form className="report-ask" onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          maxLength={1000}
          autoComplete="off"
          placeholder="e.g. Weekly report on BTC, ETH and SOL"
          aria-label="Report request"
          required
        />
        <button type="submit" disabled={busy || !input.trim()}>
          {busy ? "Writing…" : "Write report"}
        </button>
      </form>
      <div className="examples">
        {EXAMPLES.map((ex) => (
          <button key={ex} type="button" disabled={busy} onClick={() => { setInput(ex); void run(ex); }}>
            {ex}
          </button>
        ))}
      </div>

      {(busy || (steps.length > 0 && !report)) && (
        <ol className="progress" aria-label="Progress" aria-busy={busy}>
          {steps.map((s, i) => (
            <li key={i} className="done">
              {s.label}
              {s.detail.latency_ms != null && <span className="muted"> · {ms(s.detail.latency_ms)}</span>}
              {s.detail.model && <span className="muted"> · {s.detail.model}</span>}
            </li>
          ))}
          {busy && <li className="running">{steps.length ? "Working" : "Starting"}</li>}
        </ol>
      )}
      {error && <p className="fail">{error}</p>}
      {report && <ReportBody report={report} />}
    </div>
  );
}

function ReportBody({ report: r }: { report: Report }) {
  const pill = STATUS_PILL[r.status] ?? { label: r.status, tone: "" };
  if (!r.html) {
    return (
      <div className="panel">
        <div className="meta">
          <span className={`pill ${pill.tone}`}>{pill.label}</span>
        </div>
        <p>{r.message}</p>
      </div>
    );
  }
  const anchor = `rsrc-${r.request_id}`;
  const ids = new Set(r.citations.map((c) => c.id));
  const v = r.verification;
  const sub = r.markdown.split("\n\n")[1]?.replace(/^_|_$/g, "") ?? "";

  return (
    <article className="report">
      <div className="report-head">
        <div>
          <h1>{r.title}</h1>
          <p className="muted">{sub}</p>
        </div>
        <div className="report-actions">
          <button type="button" onClick={() => downloadText(r.html!, `${slug(r.title)}.html`, "text/html")}>
            Download HTML
          </button>
          <button type="button" onClick={() => downloadText(r.markdown, `${slug(r.title)}.md`, "text/markdown")}>
            Markdown
          </button>
          <button type="button" onClick={() => printHtml(r.html!)}>
            Print / PDF
          </button>
        </div>
      </div>
      <div className="meta">
        <span className={`pill ${pill.tone}`}>{pill.label}</span>
        {v && (
          <span className={`pill ${v.unsupported.length ? "bad" : ""}`}>
            {v.matched}/{v.checked} numbers verified
          </span>
        )}
        {r.trace.models.length > 0 ? (
          <span className="pill">{r.trace.models.join(" + ")}</span>
        ) : (
          <span className="pill muted-pill">templates (offline)</span>
        )}
      </div>

      <h2>Summary</h2>
      <Markdown text={r.summary} citationIds={ids} anchor={anchor} />
      <WrittenBy check={r.writer} />

      {(r.charts.price.length > 0 || r.charts.volatility.length > 0) && (
        <div className="report-charts">
          {r.charts.price.length > 0 && <PriceChart series={r.charts.price} />}
          {r.charts.volatility.length > 0 && <VolatilityChart bars={r.charts.volatility} />}
        </div>
      )}

      {r.sections.map((s) => (
        <section key={s.asset}>
          <h2>{s.name === s.asset ? s.asset : `${s.name} (${s.asset})`}</h2>
          <Markdown text={s.markdown} citationIds={ids} anchor={anchor} />
          <WrittenBy check={s.check} />
        </section>
      ))}

      {r.comparison && (
        <>
          <h2>Comparison</h2>
          <Markdown text={r.comparison} citationIds={ids} anchor={anchor} />
        </>
      )}

      <h2>Risks and caveats</h2>
      <ul>
        {r.risks.map((x, i) => (
          <li key={i}>
            <Markdown text={x} citationIds={ids} anchor={anchor} />
          </li>
        ))}
      </ul>

      {r.policy_denied.length > 0 && (
        <p className="muted">
          Not fetched (permissions): {r.policy_denied.map((d) => `${d.tool}: ${d.reason}`).join("; ")}
        </p>
      )}

      <div className="citations">
        Sources
        <ol>
          {r.citations.map((c) => {
            const href = safeUrl(c.url);
            return (
              <li key={c.id} id={`${anchor}-${c.id}`} value={c.id}>
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    {c.title}
                  </a>
                ) : (
                  c.title
                )}
              </li>
            );
          })}
        </ol>
      </div>
      <p className="muted small">Market research from public data only. This is not investment advice.</p>
      <TracePanel trace={r.trace} />
    </article>
  );
}

function WrittenBy({ check }: { check: SectionCheck | null }) {
  if (!check) return null;
  let text = "written from a template";
  if (check.mode === "llm") text = check.attempts > 1 ? "written by the LLM, fixed after verifier feedback" : "written by the LLM";
  else if (check.fell_back && check.attempts > 0) text = `template: the verifier rejected the LLM draft (${check.unsupported.join(", ")})`;
  else if (check.fell_back) text = "template: the LLM was unavailable";
  return <p className="written-by muted">{text}{check.removed.length > 0 && " · advice-like sentence removed"}</p>;
}

function PriceChart({ series }: { series: PriceSeries[] }) {
  const scale = priceScale(series);
  if (!scale) return null;
  const c = CHART;
  const y = (v: number) => c.top + (c.h - c.top - c.bottom) * (1 - (v - scale.lo) / (scale.hi - scale.lo));
  const first = series[0].points[0]?.t;
  const last = series[0].points[series[0].points.length - 1]?.t;
  return (
    <figure>
      <svg viewBox={`0 0 ${c.w} ${c.h}`} role="img" aria-label="Close price rebased to 100 at the window start">
        {scale.ticks.map((t) => (
          <g key={t}>
            <line x1={c.left} x2={c.w - c.right} y1={y(t)} y2={y(t)} className="grid" />
            <text x={c.left - 6} y={y(t) + 4} textAnchor="end" className="axis">
              {t.toFixed(1)}
            </text>
          </g>
        ))}
        {scale.lo < 100 && scale.hi > 100 && (
          <line x1={c.left} x2={c.w - c.right} y1={y(100)} y2={y(100)} className="base" />
        )}
        {series.map((s, i) => (
          <polyline key={s.asset} points={polyline(s, scale)} fill="none" stroke={COLORS[i % COLORS.length]} strokeWidth={2} />
        ))}
        {first && (
          <text x={c.left} y={c.h - 8} className="axis">
            {new Date(first).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          </text>
        )}
        {last && (
          <text x={c.w - c.right} y={c.h - 8} textAnchor="end" className="axis">
            {new Date(last).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          </text>
        )}
      </svg>
      <figcaption>
        <Legend items={series.map((s) => s.asset)} /> Close price rebased to 100 at the window start ({series[0].interval} candles).
      </figcaption>
    </figure>
  );
}

function VolatilityChart({ bars }: { bars: VolatilityBar[] }) {
  const c = CHART;
  const top = Math.max(...bars.map((b) => b.annualized_volatility_pct)) * 1.15 || 1;
  const pw = c.w - c.left - c.right;
  const ph = c.h - c.top - c.bottom;
  const slot = pw / bars.length;
  const bw = Math.min(64, slot * 0.5);
  return (
    <figure>
      <svg viewBox={`0 0 ${c.w} ${c.h}`} role="img" aria-label="Annualized volatility by asset">
        <line x1={c.left} x2={c.w - c.right} y1={c.top + ph} y2={c.top + ph} className="base" />
        {bars.map((b, i) => {
          const h = (ph * b.annualized_volatility_pct) / top;
          const x = c.left + slot * i + (slot - bw) / 2;
          return (
            <g key={b.asset}>
              <rect x={x} y={c.top + ph - h} width={bw} height={h} rx={2} fill={COLORS[i % COLORS.length]} />
              <text x={x + bw / 2} y={c.top + ph - h - 5} textAnchor="middle" className="label">
                {b.annualized_volatility_pct.toFixed(2)}%
              </text>
              <text x={x + bw / 2} y={c.top + ph + 16} textAnchor="middle" className="axis">
                {b.asset} ({b.volatility_pct.toFixed(2)}% per {b.interval})
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption>Annualized volatility; the per-candle figure is under each bar.</figcaption>
    </figure>
  );
}

function Legend({ items }: { items: string[] }) {
  return (
    <span className="legend">
      {items.map((a, i) => (
        <span key={a}>
          <i style={{ background: COLORS[i % COLORS.length] }} />
          {a}
        </span>
      ))}
    </span>
  );
}

/** Opens the standalone HTML export in a new window and prints it (the browser offers Save as PDF). */
function printHtml(html: string) {
  const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
  const w = window.open(url, "_blank");
  if (w) w.addEventListener("load", () => w.print(), { once: true });
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
