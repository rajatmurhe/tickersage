"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import TracePanel from "@/components/TracePanel";
import { ApiError, getJson } from "@/lib/api";
import { money, ms, pct } from "@/lib/format";
import { STATUS_ORDER, type StoredTrace, type TraceRow, statusPill, summarize, toStoredTrace, toolNames } from "@/lib/traces";

const LIMIT = 200;
const COLUMNS = 8;

interface Loaded {
  rows: StoredTrace[];
  refusals: Record<string, number> | null;
  refusalsError: string | null;
  loadedAt: Date;
}

/** The trace API may sit behind an admin token (X-Admin-Token) that a public page never has. */
const isPrivate = (err: unknown) => err instanceof ApiError && (err.status === 401 || err.status === 403);

const message = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

function shortTime(d: Date | null): string {
  if (!d) return "unknown";
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function TracesView() {
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [privateApi, setPrivateApi] = useState(false);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const inflight = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setLoading(true);
    const [traces, refusals] = await Promise.allSettled([
      getJson<TraceRow[]>(`/api/traces?limit=${LIMIT}`, controller.signal),
      getJson<Record<string, number>>("/api/refusals", controller.signal),
    ]);
    if (controller.signal.aborted) return;
    setLoading(false);

    if (traces.status === "rejected") {
      setPrivateApi(isPrivate(traces.reason));
      setError(message(traces.reason, "Could not load traces."));
      return;
    }
    if (!Array.isArray(traces.value)) {
      setError("/api/traces returned an unexpected shape.");
      return;
    }
    setPrivateApi(false);
    setError(null);
    setData({
      rows: traces.value.map(toStoredTrace),
      refusals: refusals.status === "fulfilled" ? refusals.value : null,
      refusalsError:
        refusals.status === "fulfilled"
          ? null
          : isPrivate(refusals.reason)
            ? "Refusal counts are private on this deployment."
            : message(refusals.reason, "Could not load refusal counts."),
      loadedAt: new Date(),
    });
  }, []);

  useEffect(() => {
    void load();
    return () => inflight.current?.abort();
  }, [load]);

  const summary = useMemo(() => summarize(data?.rows ?? []), [data]);
  const visible = useMemo(
    () => (data ? (filter === "all" ? data.rows : data.rows.filter((r) => r.status === filter)) : []),
    [data, filter],
  );

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  if (privateApi) {
    return (
      <div className="traces-page">
        <p className="intro">Traces are private on this deployment.</p>
        <p className="muted">The trace API needs an admin token, which this page does not send.</p>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="traces-page">
        {error ? (
          <div className="toolbar">
            <p className="fail">{error}</p>
            <button type="button" onClick={() => void load()} disabled={loading}>
              {loading ? "Retrying…" : "Retry"}
            </button>
          </div>
        ) : (
          <p className="intro">Loading recent requests…</p>
        )}
      </div>
    );
  }

  const cards: [string, string][] = [
    ["Requests", summary.count.toLocaleString()],
    ["Latency p50 / p95", summary.count ? `${Math.round(summary.p50)} / ${Math.round(summary.p95)} ms` : "n/a"],
    ["Total cost", money(summary.totalCost)],
    ["Avg cost / request", money(summary.avgCost)],
  ];
  const statuses = Object.keys(summary.statusCounts).filter(
    (s) => (STATUS_ORDER as string[]).includes(s) || summary.statusCounts[s] > 0,
  );
  const refusals = Object.entries(data.refusals ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="traces-page">
      <p className="intro">
        The agent&apos;s most recent requests from the trace store, newest first: what was asked, the route it took,
        the tools it called and what it cost.
      </p>

      <div className="toolbar">
        <button type="button" onClick={() => void load()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
        <span className="muted">
          {data.rows.length >= LIMIT ? `Latest ${LIMIT} requests` : `${data.rows.length} requests`} · loaded{" "}
          {data.loadedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
        </span>
        {error && <span className="fail">Refresh failed: {error}</span>}
      </div>

      <div className="cards">
        {cards.map(([k, v]) => (
          <div className="card" key={k}>
            <b>{v}</b>
            <span>{k}</span>
          </div>
        ))}
      </div>

      <div className="split">
        <section className="panel">
          <h2>Status mix</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Status</th>
                  <th className="num">Requests</th>
                  <th className="num">Share</th>
                </tr>
              </thead>
              <tbody>
                {statuses.map((s) => {
                  const pill = statusPill(s);
                  const n = summary.statusCounts[s];
                  return (
                    <tr key={s}>
                      <td>
                        <span className={`pill ${pill.tone}`}>{pill.label}</span>
                      </td>
                      <td className="num">{n}</td>
                      <td className="num muted">{summary.count ? pct(n / summary.count, 0) : "n/a"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <h2>Refusals by category</h2>
          {data.refusalsError ? (
            <p className="muted">{data.refusalsError}</p>
          ) : refusals.length ? (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Category</th>
                    <th className="num">Refusals</th>
                  </tr>
                </thead>
                <tbody>
                  {refusals.map(([category, n]) => (
                    <tr key={category}>
                      <td>{category}</td>
                      <td className="num">{n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">No refusals recorded.</p>
          )}
          <p className="muted note">Counted over every stored trace, not only the rows loaded here.</p>
        </section>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>Recent requests</h2>
          <label>
            Status{" "}
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="all">all ({summary.count})</option>
              {statuses.map((s) => (
                <option key={s} value={s}>
                  {statusPill(s).label} ({summary.statusCounts[s]})
                </option>
              ))}
            </select>
          </label>
        </div>

        {data.rows.length === 0 ? (
          <p className="muted">
            No traces yet. Ask a question in the <Link href="/">chat</Link> and it will show up here.
          </p>
        ) : visible.length === 0 ? (
          <p className="muted">No loaded requests have this status.</p>
        ) : (
          <div className="table-wrap">
            <table className="traces">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Question</th>
                  <th>Intent</th>
                  <th>Status</th>
                  <th>Tools</th>
                  <th className="num">Latency</th>
                  <th className="num">Cost</th>
                  <th className="num">Faithfulness</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((t) => {
                  const open = expanded.has(t.request_id);
                  const pill = statusPill(t.status);
                  const tools = toolNames(t);
                  const failed = t.tools.filter((x) => !x.ok).length;
                  const detailId = `trace-${t.request_id}`;
                  return (
                    <Fragment key={t.request_id}>
                      <tr
                        className={`trace-row${open ? " open" : ""}`}
                        onClick={(e) => {
                          // Let people select question text without toggling the row.
                          const onButton = (e.target as HTMLElement).closest("button");
                          if (!onButton && window.getSelection()?.toString()) return;
                          toggle(t.request_id);
                        }}
                      >
                        <td>
                          <button type="button" className="row-toggle" aria-expanded={open} aria-controls={detailId}>
                            <span className="chev" aria-hidden="true">
                              ▸
                            </span>
                            <time
                              dateTime={t.createdAt?.toISOString()}
                              title={t.createdAt ? t.createdAt.toLocaleString() : undefined}
                            >
                              {shortTime(t.createdAt)}
                            </time>
                          </button>
                        </td>
                        <td className="q">
                          <span className="q-text">{t.question}</span>
                        </td>
                        <td className="mono" data-label="intent">
                          {t.intent ?? <span className="muted">n/a</span>}
                        </td>
                        <td>
                          <span className={`pill ${pill.tone}`}>{pill.label}</span>
                          {t.fell_back && <span className="pill warn">fallback</span>}
                        </td>
                        <td className="mono tools" data-label="tools">
                          {tools.length ? tools.join(", ") : <span className="muted">none</span>}
                          {failed > 0 && <span className="pill bad">{failed} failed</span>}
                        </td>
                        <td className="num" data-label="latency">
                          {ms(t.latency_ms)}
                        </td>
                        <td className="num" data-label="cost">
                          {money(t.cost_usd)}
                        </td>
                        <td
                          className={`num${t.faithfulness != null && t.faithfulness < 1 ? " fail" : ""}`}
                          data-label="faithfulness"
                        >
                          {pct(t.faithfulness, 0)}
                        </td>
                      </tr>
                      {open && (
                        <tr className="trace-detail" id={detailId}>
                          <td colSpan={COLUMNS}>
                            <div className="trace-cell">
                              <TracePanel trace={t} open />
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
