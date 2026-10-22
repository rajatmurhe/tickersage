"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Markdown from "@/components/Markdown";
import {
  ACTION_LABELS,
  type AlertEvent,
  type CheckResult,
  type CompileResult,
  MAX_TEXT,
  type Rule,
  type Status,
  type WatchItem,
  alertsApi,
  cleanAnswers,
  cooldownLabel,
  deliveryNotes,
  timeAgo,
} from "@/lib/alerts";

const EXAMPLES = [
  "Tell me when ETH RSI(14) on 1h drops below 30",
  "SOL moves more than 5% in 24h with negative news",
  "Alert me if BTC goes above $90,000, at most once a day",
  "Golden cross on BTC daily",
];
const FEED_REFRESH_MS = 30_000;

const message = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

export default function Alerts() {
  const [text, setText] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [compiledText, setCompiledText] = useState<string | null>(null);
  const [compiled, setCompiled] = useState<CompileResult | null>(null);
  const [channels, setChannels] = useState<string[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [events, setEvents] = useState<AlertEvent[]>([]);
  const [watch, setWatch] = useState<WatchItem[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const inflight = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [r, e, w, s] = await Promise.all([
        alertsApi<Rule[]>("/api/alerts/rules"),
        alertsApi<AlertEvent[]>("/api/alerts/events?limit=50"),
        alertsApi<WatchItem[]>("/api/watchlist"),
        alertsApi<Status>("/api/alerts/status"),
      ]);
      setRules(r);
      setEvents(e);
      setWatch(w);
      setStatus(s);
    } catch (err) {
      setError(message(err, "Could not load alerts."));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), FEED_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  async function compile(t: string, withAnswers: Record<string, string>) {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setBusy("compile");
    setError(null);
    setNotice(null);
    try {
      const r = await alertsApi<CompileResult>("/api/alerts/compile", {
        body: { text: t, answers: cleanAnswers(withAnswers) },
        signal: controller.signal,
      });
      setCompiled(r);
      setCompiledText(t);
    } catch (err) {
      if (!controller.signal.aborted) setError(message(err, "Could not compile the alert."));
    } finally {
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  function submitText(e?: FormEvent, value = text) {
    e?.preventDefault();
    const t = value.trim();
    if (!t || busy) return;
    const keep = t === compiledText ? answers : {};
    if (t !== compiledText) setAnswers({});
    void compile(t, keep);
  }

  function submitAnswers(e: FormEvent) {
    e.preventDefault();
    if (compiledText && !busy) void compile(compiledText, answers);
  }

  async function act<T>(key: string, fn: () => Promise<T>, done?: (r: T) => void) {
    setBusy(key);
    setError(null);
    try {
      const r = await fn();
      done?.(r);
      await refresh();
    } catch (err) {
      setError(message(err, "Something went wrong."));
    } finally {
      setBusy(null);
    }
  }

  const save = () =>
    compiled?.spec &&
    act(
      "save",
      () =>
        alertsApi<Rule>("/api/alerts/rules", {
          body: { text: compiled.text, spec: compiled.spec, source: compiled.source, channels: ["in_app", ...channels] },
        }),
      () => {
        setNotice("Alert saved. It is checked on the schedule below; use “Check now” to run it immediately.");
        setCompiled(null);
        setCompiledText(null);
        setText("");
        setAnswers({});
      },
    );

  const checkNow = (rule: Rule) =>
    act(`check-${rule.id}`, () => alertsApi<CheckResult>(`/api/alerts/rules/${rule.id}/check`, { method: "POST" }), (r) =>
      setNotice(
        r.action === "fire"
          ? `${rule.spec.symbol}: the alert fired. The brief is in the feed.`
          : `${rule.spec.symbol}: ${ACTION_LABELS[r.action]}.${r.evaluation.errors.length ? ` ${r.evaluation.errors[0]}` : ""}`,
      ),
    );

  const unread = events.filter((e) => !e.read).length;
  const external = status ? (["telegram", "email"] as const).filter((c) => status.channels[c]) : [];

  return (
    <div className="strategy-page alerts-page">
      <p className="intro">
        Describe a market condition in plain words. TickerSage turns it into a rule, checks it on a schedule with the
        read-only market tools, and when it fires writes a short “why it moved” brief in which every number is checked
        against the data. Alerts only notify: nothing here trades, and an alert is not a signal to trade.
      </p>

      <WatchlistBar items={watch} busy={busy} onAdd={(s) => act("watch", () => alertsApi("/api/watchlist", { body: { symbol: s } }))}
        onRemove={(s) => act(`unwatch-${s}`, () => alertsApi(`/api/watchlist/${encodeURIComponent(s)}`, { method: "DELETE" }))}
        onPick={(s) => setText(`Tell me when ${s} `)} />

      <form className="idea" onSubmit={submitText}>
        <label htmlFor="alert-text">New alert</label>
        <textarea
          id="alert-text"
          value={text}
          maxLength={MAX_TEXT}
          rows={2}
          placeholder="Tell me when ETH RSI(14) on 1h drops below 30"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitText();
          }}
        />
        <div className="actions">
          <button type="submit" className="btn" disabled={!text.trim() || busy !== null}>
            {busy === "compile" ? "Reading…" : "Create rule"}
          </button>
          <span className="muted hint">RSI · price vs SMA · SMA crosses · % moves · price levels · news tone</span>
        </div>
      </form>

      {!compiled && (
        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" disabled={busy !== null} onClick={() => { setText(ex); submitText(undefined, ex); }}>
              {ex}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="panel" role="alert">
          <p className="fail">{error}</p>
        </div>
      )}
      {notice && (
        <div className="panel" role="status">
          <p className="ok">{notice}</p>
        </div>
      )}

      {compiled?.status === "rejected" && (
        <div className="panel" role="alert">
          <h2>
            Not created <span className="pill bad">{compiled.rejection_category}</span>
          </h2>
          <p>{compiled.rejection}</p>
        </div>
      )}

      {compiled && compiled.status !== "rejected" && (
        <>
          {compiled.status === "needs_clarification" && (
            <form className="panel questions" onSubmit={submitAnswers}>
              <h2>A few details first</h2>
              <p className="muted note-top">Missing parts are asked about, never guessed.</p>
              {compiled.questions.map((q) => (
                <div className="q" key={q.field}>
                  <label htmlFor={`aq-${q.field}`}>{q.question}</label>
                  <input
                    id={`aq-${q.field}`}
                    value={answers[q.field] ?? ""}
                    maxLength={200}
                    placeholder={`e.g. ${q.example}`}
                    autoComplete="off"
                    onChange={(e) => setAnswers((a) => ({ ...a, [q.field]: e.target.value }))}
                  />
                </div>
              ))}
              <div className="actions">
                <button type="submit" className="btn"
                  disabled={busy !== null || !compiled.questions.some((q) => (answers[q.field] ?? "").trim())}>
                  {busy === "compile" ? "Reading…" : "Continue"}
                </button>
              </div>
            </form>
          )}
          <div className="panel">
            <div className="panel-head">
              <h2>{compiled.spec ? "Rule" : "Rule so far"}</h2>
              <span className="pill muted-pill" title={compiled.fallback_reason ?? undefined}>
                {compiled.source === "llm" && compiled.llm ? `read by ${compiled.llm.model}` : "read by rules"}
              </span>
            </div>
            <ul className="summary-list">
              {compiled.summary.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            {(compiled.notes.length > 0 || compiled.dropped.length > 0) && (
              <details className="trace">
                <summary>How the text was read ({compiled.notes.length + compiled.dropped.length})</summary>
                <ul className="summary-list">
                  {[...compiled.dropped, ...compiled.notes].map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              </details>
            )}
            {compiled.spec && (
              <>
                {external.length > 0 && (
                  <fieldset className="channels">
                    <legend>Also send to</legend>
                    {external.map((c) => (
                      <label key={c}>
                        <input type="checkbox" checked={channels.includes(c)}
                          onChange={(e) => setChannels((cs) => (e.target.checked ? [...cs, c] : cs.filter((x) => x !== c)))} />
                        {c === "telegram" ? "Telegram" : "Email"}
                      </label>
                    ))}
                    {status?.external_channels_need_admin && <span className="muted hint">needs the admin token</span>}
                  </fieldset>
                )}
                <div className="actions">
                  <button type="button" className="btn" onClick={save} disabled={busy !== null}>
                    {busy === "save" ? "Saving…" : "Save alert"}
                  </button>
                  <span className="muted hint">Shows up in the feed below{external.length ? " and on the channels you pick" : ""}.</span>
                </div>
              </>
            )}
          </div>
        </>
      )}

      <RulesPanel rules={rules} status={status} busy={busy} onCheck={checkNow}
        onToggle={(r) => act(`toggle-${r.id}`, () => alertsApi(`/api/alerts/rules/${r.id}/enabled`, { body: { enabled: !r.enabled } }))}
        onDelete={(r) => act(`delete-${r.id}`, () => alertsApi(`/api/alerts/rules/${r.id}`, { method: "DELETE" }))} />

      <section className="panel" aria-labelledby="feed-title">
        <div className="panel-head">
          <h2 id="feed-title">
            Alert feed {unread > 0 && <span className="pill">{unread} new</span>}
          </h2>
          {unread > 0 && (
            <div className="toolbar">
              <button type="button" disabled={busy !== null}
                onClick={() => act("read", () => alertsApi("/api/alerts/events/read", { body: {} }))}>
                Mark all read
              </button>
            </div>
          )}
        </div>
        {events.length === 0 ? (
          <p className="muted">No alerts yet. When a rule fires, its brief appears here.</p>
        ) : (
          <ol className="feed">
            {events.map((e) => (
              <li key={e.id} className={e.read ? "" : "unread"}>
                <div className="feed-meta">
                  <b>{e.symbol}</b>
                  <time dateTime={e.created_at} title={e.created_at}>{timeAgo(e.created_at)}</time>
                  <span className="pill muted-pill" title={e.verification.fallback_reason ?? undefined}>
                    {e.brief_source === "llm" ? "written by the model" : "template"} · {e.verification.matched}/
                    {e.verification.checked} numbers verified
                  </span>
                </div>
                <div className="answer">
                  <Markdown text={e.brief} />
                </div>
                {deliveryNotes(e.deliveries).length > 0 && (
                  <p className="muted note">{deliveryNotes(e.deliveries).join(" · ")}</p>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      <p className="disclaimer">{status?.disclaimer ?? compiled?.disclaimer}</p>
    </div>
  );
}

function RulesPanel({ rules, status, busy, onCheck, onToggle, onDelete }: {
  rules: Rule[];
  status: Status | null;
  busy: string | null;
  onCheck: (r: Rule) => void;
  onToggle: (r: Rule) => void;
  onDelete: (r: Rule) => void;
}) {
  const schedule = status
    ? status.scheduler
      ? `Checked every ${status.interval_s < 60 ? `${Math.round(status.interval_s)} s` : `${Math.round(status.interval_s / 60)} min`}${status.last_run ? `, last pass ${timeAgo(status.last_run.at)}` : ""}.`
      : "The scheduler is off on this server: use “Check now”."
    : "";
  return (
    <section className="panel" aria-labelledby="rules-title">
      <div className="panel-head">
        <h2 id="rules-title">Your rules</h2>
        <span className="muted hint">{schedule}</span>
      </div>
      {rules.length === 0 ? (
        <p className="muted">No rules yet.</p>
      ) : (
        <ul className="rules">
          {rules.map((r) => (
            <li key={r.id} className={r.enabled ? "" : "paused"}>
              <div className="rule-text">
                <b>{r.spec.symbol}</b> <span>{r.summary[1] ?? r.text}</span>
                <span className="muted rule-meta">
                  “{r.text.length > 120 ? `${r.text.slice(0, 117)}…` : r.text}” · at most once every{" "}
                  {cooldownLabel(r.spec.cooldown_minutes)} · {r.enabled ? (r.armed ? "armed" : "waiting to re-arm") : "paused"} ·
                  last check {timeAgo(r.last_checked_at)}
                  {r.last_action ? ` (${ACTION_LABELS[r.last_action]})` : ""} · fired {r.fire_count}×
                </span>
                {r.last_error && <span className="fail rule-meta">{r.last_error}</span>}
              </div>
              <div className="toolbar">
                <button type="button" disabled={busy !== null || !r.enabled} onClick={() => onCheck(r)}>
                  {busy === `check-${r.id}` ? "Checking…" : "Check now"}
                </button>
                <button type="button" disabled={busy !== null} onClick={() => onToggle(r)}>
                  {r.enabled ? "Pause" : "Resume"}
                </button>
                <button type="button" disabled={busy !== null} onClick={() => onDelete(r)} aria-label={`Delete the ${r.spec.symbol} rule`}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function WatchlistBar({ items, busy, onAdd, onRemove, onPick }: {
  items: WatchItem[];
  busy: string | null;
  onAdd: (symbol: string) => void;
  onRemove: (symbol: string) => void;
  onPick: (symbol: string) => void;
}) {
  const [symbol, setSymbol] = useState("");
  return (
    <section className="watchlist" aria-label="Watchlist">
      <span className="muted">Watchlist</span>
      {items.map((i) => (
        <span key={i.symbol} className="chip">
          <button type="button" className="chip-main" onClick={() => onPick(i.symbol)} title={`Write an alert for ${i.symbol}`}>
            {i.symbol}
            {i.rules > 0 && <small> · {i.rules}</small>}
          </button>
          <button type="button" className="chip-x" disabled={busy !== null} onClick={() => onRemove(i.symbol)}
            aria-label={`Remove ${i.symbol} from the watchlist`}>
            ×
          </button>
        </span>
      ))}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (symbol.trim()) {
            onAdd(symbol.trim());
            setSymbol("");
          }
        }}
      >
        <input value={symbol} maxLength={20} placeholder="add ETH" aria-label="Add an asset to the watchlist"
          onChange={(e) => setSymbol(e.target.value)} />
      </form>
    </section>
  );
}
