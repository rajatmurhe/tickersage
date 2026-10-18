"use client";

import { useEffect, useState } from "react";
import { ApiError, getJson } from "@/lib/api";
import { pct } from "@/lib/format";
import type { EvalRun } from "@/lib/types";

export default function EvalsView() {
  const [run, setRun] = useState<EvalRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getJson<EvalRun>("/api/evals/latest", controller.signal)
      .then(setRun)
      .catch((err) => {
        if (controller.signal.aborted) return;
        setError(
          err instanceof ApiError && err.status === 404
            ? "No eval results yet. Run: python -m evals.run_evals"
            : err instanceof Error
              ? err.message
              : "Could not load eval results.",
        );
      });
    return () => controller.abort();
  }, []);

  if (error) return <p className="intro fail">{error}</p>;
  if (!run) return <p className="intro">Loading the latest replay eval run…</p>;

  const s = run.summary;
  const c = run.config;
  const cards: [string, string][] = [
    ["Pass rate", pct(s.pass_rate)],
    ["Tool selection", pct(s.tool_selection_accuracy)],
    ["Number faithfulness", pct(s.number_faithfulness)],
    ["Key numbers present", pct(s.key_number_accuracy)],
    ["Refusal recall", pct(s.refusal_recall)],
    ["False refusals", pct(s.false_refusal_rate)],
    ["Injection resistance", pct(s.injection_resistance)],
    ["Citation rate (concepts)", pct(s.citation_rate)],
    ["Latency p50 / p95", `${s.latency_p50_ms} / ${s.latency_p95_ms} ms`],
    ["Avg cost / query", `$${(s.avg_cost_usd ?? 0).toFixed(5)}`],
    ["Judge score", s.judge_score == null ? "not run" : pct(s.judge_score)],
  ];
  const categories = Object.entries(run.by_category);
  const taxonomy = Object.entries(run.failure_taxonomy).sort((a, b) => b[1] - a[1]);
  const failing = run.cases.filter((x) => !x.passed);

  return (
    <>
      <p className="intro">
        Run <span className="mono">{run.run_id}</span> · {run.cases_run} cases · LLM {c.llm} · routing {c.routing_mode} ·
        verifier {c.verifier ? "on" : "off"} · tools {c.tool_mode}
        {s.numbers_checked ? ` · ${s.numbers_checked.toLocaleString()} numbers checked` : ""}
      </p>

      <div className="cards">
        {cards.map(([k, v]) => (
          <div className="card" key={k}>
            <b>{v}</b>
            <span>{k}</span>
          </div>
        ))}
      </div>

      <section className="panel">
        <h2>By category</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Category</th>
                <th className="num">Cases</th>
                <th className="num">Passed</th>
                <th className="num">Pass rate</th>
              </tr>
            </thead>
            <tbody>
              {categories.map(([name, v]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td className="num">{v.n}</td>
                  <td className="num">{v.passed}</td>
                  <td className={`num ${v.passed === v.n ? "ok" : "fail"}`}>{pct(v.pass_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <h2>Failure taxonomy</h2>
        {taxonomy.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Failure type</th>
                  <th className="num">Count</th>
                </tr>
              </thead>
              <tbody>
                {taxonomy.map(([name, count]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td className="num">{count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">No failures.</p>
        )}
      </section>

      <section className="panel">
        <h2>Failing cases</h2>
        {failing.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Question</th>
                  <th>Failures</th>
                </tr>
              </thead>
              <tbody>
                {failing.map((x) => (
                  <tr key={x.id}>
                    <td className="mono">{x.id}</td>
                    <td className="wrap">{x.question}</td>
                    <td className="wrap">{x.failures.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">Every case passed.</p>
        )}
      </section>
    </>
  );
}
