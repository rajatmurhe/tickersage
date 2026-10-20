"use client";

import { type FormEvent, type PointerEvent, useMemo, useRef, useState } from "react";
import {
  type BacktestResponse,
  type BacktestResult,
  type CompileResult,
  EXIT_LABELS,
  type Finding,
  cleanAnswers,
  flowRows,
  hasErrors,
  nearestIndex,
  parseFlow,
  postJson,
  signedPct,
  sparkline,
  usd,
  utc,
} from "@/lib/strategy";

const EXAMPLES = [
  "Buy ETH on the 4h chart when RSI drops below 30. Take profit at 5%, stop loss at 3%, 10% of the balance.",
  "Golden cross on BTC daily candles, take profit 20%, stop loss 8%, 20% of the balance",
  "SOL 1h: buy when price falls 4% within 6 hours. Sell at +3% or stop out at -2%. Max hold 24 hours. Size 15%.",
  "buy ETH when RSI drops below 30, take profit at 5%, stop at 3%",
];
const MAX_TRADES_SHOWN = 50;

const message = (err: unknown, fallback: string) => (err instanceof Error ? err.message : fallback);

export default function StrategyStudio() {
  const [idea, setIdea] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [compiledIdea, setCompiledIdea] = useState<string | null>(null);
  const [compiled, setCompiled] = useState<CompileResult | null>(null);
  const [backtest, setBacktest] = useState<BacktestResponse | null>(null);
  const [busy, setBusy] = useState<"compile" | "backtest" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef<AbortController | null>(null);

  async function compile(text: string, withAnswers: Record<string, string>) {
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setBusy("compile");
    setError(null);
    setBacktest(null);
    try {
      const r = await postJson<CompileResult>(
        "/api/strategy/compile",
        { text, answers: cleanAnswers(withAnswers) },
        controller.signal,
      );
      setCompiled(r);
      setCompiledIdea(text);
    } catch (err) {
      if (!controller.signal.aborted) setError(message(err, "Could not compile the idea."));
    } finally {
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  function submitIdea(e?: FormEvent, text = idea) {
    e?.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    // Answers belong to the idea they were given for; a new idea starts over.
    const keep = t === compiledIdea ? answers : {};
    if (t !== compiledIdea) setAnswers({});
    void compile(t, keep);
  }

  function submitAnswers(e: FormEvent) {
    e.preventDefault();
    if (compiledIdea && !busy) void compile(compiledIdea, answers);
  }

  async function runBacktest() {
    if (!compiled?.spec || busy) return;
    inflight.current?.abort();
    const controller = new AbortController();
    inflight.current = controller;
    setBusy("backtest");
    setError(null);
    try {
      const r = await postJson<BacktestResponse>(
        "/api/strategy/backtest",
        { spec: compiled.spec, text: compiled.lint_text },
        controller.signal,
      );
      setBacktest(r);
    } catch (err) {
      if (!controller.signal.aborted) setError(message(err, "Could not run the backtest."));
    } finally {
      if (!controller.signal.aborted) setBusy(null);
    }
  }

  const blocked = compiled ? hasErrors(compiled.findings) : false;

  return (
    <div className="strategy-page">
      <p className="intro">
        Describe a rule-based idea in plain words. Strategy Studio turns it into a spec, checks it for the usual
        mistakes, draws the rules and backtests them on historical Binance candles. It only backtests: nothing here
        places orders, and it never says whether a strategy is good.
      </p>

      <form className="idea" onSubmit={submitIdea}>
        <label htmlFor="idea">Your idea</label>
        <textarea
          id="idea"
          value={idea}
          maxLength={1000}
          rows={3}
          placeholder="Buy ETH on the 4h chart when RSI drops below 30, take profit 5%, stop loss 3%, 10% of the balance"
          onChange={(e) => setIdea(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitIdea();
          }}
        />
        <div className="actions">
          <button type="submit" className="btn" disabled={!idea.trim() || busy !== null}>
            {busy === "compile" ? "Compiling…" : "Compile"}
          </button>
          <span className="muted hint">Long-only spot rules on one asset · 15m, 1h, 4h or 1d candles</span>
        </div>
      </form>

      {!compiled && (
        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              disabled={busy !== null}
              onClick={() => {
                setIdea(ex);
                submitIdea(undefined, ex);
              }}
            >
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

      {compiled?.status === "rejected" && (
        <div className="panel" role="alert">
          <h2>
            Not compiled <span className="pill bad">{compiled.rejection_category}</span>
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
                  <label htmlFor={`q-${q.field}`}>{q.question}</label>
                  <input
                    id={`q-${q.field}`}
                    value={answers[q.field] ?? ""}
                    maxLength={200}
                    placeholder={`e.g. ${q.example}`}
                    autoComplete="off"
                    onChange={(e) => setAnswers((a) => ({ ...a, [q.field]: e.target.value }))}
                  />
                </div>
              ))}
              <div className="actions">
                <button
                  type="submit"
                  className="btn"
                  disabled={busy !== null || !compiled.questions.some((q) => (answers[q.field] ?? "").trim())}
                >
                  {busy === "compile" ? "Compiling…" : "Continue"}
                </button>
              </div>
            </form>
          )}

          <SpecPanel compiled={compiled} />
          {compiled.findings.length > 0 && <Findings findings={compiled.findings} />}
          {compiled.mermaid && <FlowPanel source={compiled.mermaid} />}

          {compiled.spec && (
            <div className="actions">
              <button type="button" className="btn" onClick={runBacktest} disabled={busy !== null || blocked}>
                {busy === "backtest" ? "Running…" : "Run backtest"}
              </button>
              <span className="muted hint">
                {blocked
                  ? "Fix the errors above first: edit the idea and compile again."
                  : "On the last 500 candles, with fees and slippage."}
              </span>
            </div>
          )}
        </>
      )}

      {backtest && <BacktestPanel response={backtest} />}

      {(compiled?.disclaimer || backtest?.disclaimer) && (
        <p className="disclaimer">{backtest?.disclaimer ?? compiled?.disclaimer}</p>
      )}
    </div>
  );
}

function SpecPanel({ compiled }: { compiled: CompileResult }) {
  const ready = compiled.status === "ok";
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>{ready ? "Spec" : "Spec so far"}</h2>
        <span className="pill muted-pill" title={compiled.fallback_reason ?? undefined}>
          {compiled.source === "llm" && compiled.llm ? `compiled by ${compiled.llm.model}` : "compiled by rules"}
        </span>
      </div>
      {compiled.summary.length > 0 ? (
        <ul className="summary-list">
          {compiled.summary.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">Nothing could be read from the idea yet.</p>
      )}
      {compiled.notes.length > 0 && (
        <details className="trace">
          <summary>How the text was read ({compiled.notes.length})</summary>
          <ul className="summary-list">
            {compiled.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </details>
      )}
      {compiled.spec && (
        <details className="trace">
          <summary>Spec JSON</summary>
          <pre className="code-block">{JSON.stringify(compiled.spec, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

function Findings({ findings }: { findings: Finding[] }) {
  return (
    <div className="panel">
      <h2>Lint findings</h2>
      <ul className="findings">
        {findings.map((f) => (
          <li key={`${f.rule}-${f.field}-${f.message}`}>
            <span className={`pill ${f.severity === "error" ? "bad" : "warn"}`}>{f.severity}</span>
            <span>
              <code>{f.rule}</code> {f.message}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FlowPanel({ source }: { source: string }) {
  const rows = useMemo(() => flowRows(parseFlow(source)), [source]);
  return (
    <div className="panel">
      <h2>The rules as a flow</h2>
      <div className="flow" role="list" aria-label="Strategy flow">
        {rows.map((row, i) => (
          <div key={row.nodes.map((n) => n.node.id).join("-")} role="listitem">
            {i > 0 && (
              <div className="flow-arrow" aria-hidden="true">
                ↓{row.nodes.length === 1 && row.nodes[0].via ? ` ${row.nodes[0].via}` : ""}
              </div>
            )}
            <div className="flow-row">
              {row.nodes.map(({ node, via }) => (
                <div key={node.id} className={`flow-node ${node.shape}`}>
                  {row.nodes.length > 1 && via && <span className="edge">{via}</span>}
                  {node.lines.map((line, k) => (k === 0 ? <b key={k}>{line}</b> : <span key={k}>{line}</span>))}
                </div>
              ))}
            </div>
            {row.loops.map((loop) => (
              <div key={`${loop.to.id}-${loop.label}`} className="flow-loop">
                ↺ {loop.label ? `${loop.label}: ` : ""}back to “{loop.to.lines[0]}”
              </div>
            ))}
          </div>
        ))}
      </div>
      <details className="trace">
        <summary>Mermaid source</summary>
        <pre className="code-block">{source}</pre>
      </details>
    </div>
  );
}

function BacktestPanel({ response }: { response: BacktestResponse }) {
  if (response.status !== "ok" || !response.result) {
    return (
      <div className="panel" role="alert">
        <h2>
          Backtest not run <span className={`pill ${response.status === "blocked" ? "bad" : "warn"}`}>{response.status}</span>
        </h2>
        <p>{response.error}</p>
        {response.findings.length > 0 && (
          <ul className="findings">
            {response.findings.map((f) => (
              <li key={`${f.rule}-${f.message}`}>
                <span className={`pill ${f.severity === "error" ? "bad" : "warn"}`}>{f.severity}</span>
                <span>{f.message}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  const r = response.result;
  const m = r.metrics;
  return (
    <div className="panel">
      <h2>Backtest results</h2>
      <p className="muted note-top">
        {r.symbol}/USDT {r.interval} candles from {utc(r.start)} to {utc(r.end)} UTC ({r.candles} candles after{" "}
        {r.warmup_candles} of indicator warm-up) · {r.source ?? "Binance"}
      </p>
      <div className="cards">
        <Card label="Total return" value={signedPct(m.total_return_pct)} />
        <Card label="Buy and hold, same window" value={signedPct(m.buy_and_hold_return_pct)} />
        <Card label="Max drawdown" value={signedPct(m.max_drawdown_pct)} />
        <Card label="Trades" value={String(m.trade_count)} />
        <Card label="Win rate" value={m.win_rate_pct == null ? "n/a" : `${m.win_rate_pct.toFixed(1)}%`} />
        <Card label="Average trade" value={signedPct(m.avg_trade_pct)} />
        <Card label="Exposure (time in a trade)" value={`${m.exposure_pct.toFixed(1)}%`} />
        <Card label="Fees paid" value={usd(m.fees_paid)} />
      </div>
      <EquityChart result={r} />
      {r.notes.length > 0 && (
        <ul className="summary-list notes">
          {r.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      {r.trades.length > 0 && (
        <details className="trace" open={r.trades.length <= 10}>
          <summary>Trades ({r.trades.length})</summary>
          <div className="table-wrap">
            <table className="trades">
              <thead>
                <tr>
                  <th>Entry (UTC)</th>
                  <th className="num">Entry price</th>
                  <th>Exit (UTC)</th>
                  <th className="num">Exit price</th>
                  <th>Exit</th>
                  <th className="num">Candles</th>
                  <th className="num">P&amp;L</th>
                  <th className="num">Return</th>
                </tr>
              </thead>
              <tbody>
                {r.trades.slice(0, MAX_TRADES_SHOWN).map((t) => (
                  <tr key={t.entry_time}>
                    <td>{utc(t.entry_time)}</td>
                    <td className="num">{t.entry_price}</td>
                    <td>{utc(t.exit_time)}</td>
                    <td className="num">{t.exit_price}</td>
                    <td>{EXIT_LABELS[t.exit_reason]}</td>
                    <td className="num">{t.bars_held}</td>
                    <td className="num">{usd(t.pnl)}</td>
                    <td className="num">{signedPct(t.return_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {r.trades.length > MAX_TRADES_SHOWN && (
            <p className="muted note">First {MAX_TRADES_SHOWN} of {r.trades.length} trades.</p>
          )}
        </details>
      )}
    </div>
  );
}

function Card({ label, value }: { label: string; value: string }) {
  return (
    <div className="card">
      <b>{value}</b>
      <span>{label}</span>
    </div>
  );
}

const W = 600;
const H = 96;

function EquityChart({ result }: { result: BacktestResult }) {
  const points = result.equity_curve;
  const initial = result.metrics.initial_balance;
  const spark = useMemo(() => sparkline(points.map((p) => p.equity), W, H, initial), [points, initial]);
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return null;
  const shown = hover ?? points.length - 1;

  function onMove(e: PointerEvent<SVGSVGElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    setHover(nearestIndex(spark.xs, ((e.clientX - box.left) / box.width) * W));
  }

  return (
    <figure className="equity">
      <figcaption>
        <span className="muted">Demo balance {hover === null ? "at the end" : `at ${utc(points[shown].time)} UTC`}</span>{" "}
        <b>{usd(points[shown].equity)}</b>
      </figcaption>
      <svg
        className="spark"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Demo balance from ${usd(initial)} to ${usd(result.metrics.final_balance)} over the backtest`}
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
      >
        {spark.baseline !== null && <line className="base" x1={0} x2={W} y1={spark.baseline} y2={spark.baseline} />}
        <path className="line" d={spark.path} />
        {hover !== null && <line className="cross" x1={spark.xs[hover]} x2={spark.xs[hover]} y1={0} y2={H} />}
      </svg>
      <p className="muted axis">
        <span>{utc(points[0].time).slice(0, 10)}</span>
        <span>dashed line: starting balance {usd(initial)}</span>
        <span>{utc(points[points.length - 1].time).slice(0, 10)}</span>
      </p>
    </figure>
  );
}
