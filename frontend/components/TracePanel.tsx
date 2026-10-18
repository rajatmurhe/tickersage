import type { Trace } from "@/lib/types";
import { money, ms, pct } from "@/lib/format";

/** Collapsible per-answer trace: tool calls, graph steps, tokens, cost and number faithfulness. */
export default function TracePanel({ trace: t, open }: { trace: Trace; open?: boolean }) {
  const stats: [string, string][] = [
    ["intent", t.intent ?? "n/a"],
    ["latency", ms(t.latency_ms)],
    ["cost", money(t.cost_usd)],
    ["tokens in / out", `${t.input_tokens} / ${t.output_tokens}`],
    ["number faithfulness", t.faithfulness == null ? "n/a" : pct(t.faithfulness, 0)],
    ["models", t.models.length ? t.models.join(", ") : "none (offline)"],
    ["provider fallback", t.fell_back ? "yes" : "no"],
  ];
  const calls = `${t.tools.length} tool call${t.tools.length === 1 ? "" : "s"}`;

  return (
    <details className="trace" open={open}>
      <summary>
        Trace · {calls} · {ms(t.latency_ms)} · {money(t.cost_usd)}
      </summary>
      <div className="trace-grid">
        {stats.map(([k, v]) => (
          <div className="stat" key={k}>
            <b>{v}</b>
            <span>{k}</span>
          </div>
        ))}
      </div>
      {t.unsupported_numbers.length > 0 && (
        <p className="fail">Unsupported numbers: {t.unsupported_numbers.join(", ")}</p>
      )}

      {t.tools.length > 0 && (
        <div className="table-wrap">
          <table>
            <caption>Tool calls</caption>
            <thead>
              <tr>
                <th>tool</th>
                <th>args</th>
                <th>result</th>
                <th>time</th>
                <th>source</th>
              </tr>
            </thead>
            <tbody>
              {t.tools.map((x) => (
                <tr key={x.id}>
                  <td className="mono">{x.tool}</td>
                  <td className="mono json">{JSON.stringify(x.args)}</td>
                  <td>
                    {x.ok ? (
                      <span className="ok">ok</span>
                    ) : (
                      <span className="fail" title={x.error_message ?? undefined}>
                        {x.error ?? "failed"}
                      </span>
                    )}
                  </td>
                  <td>{x.cached ? <span className="pill">cache</span> : ms(x.latency_ms)}</td>
                  <td>{x.source ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="table-wrap">
        <table>
          <caption>Graph steps</caption>
          <thead>
            <tr>
              <th>step</th>
              <th>time</th>
              <th>model</th>
              <th>tokens in / out</th>
              <th>cost</th>
              <th>detail</th>
            </tr>
          </thead>
          <tbody>
            {t.steps.map((s, i) => (
              <tr key={i}>
                <td className="mono">{s.name}</td>
                <td>{ms(s.latency_ms)}</td>
                <td>
                  {s.model ?? ""}
                  {s.fell_back && <span className="pill warn">fallback</span>}
                </td>
                <td>{s.input_tokens || s.output_tokens ? `${s.input_tokens} / ${s.output_tokens}` : ""}</td>
                <td>{s.cost_usd ? money(s.cost_usd) : ""}</td>
                <td className="mono json">{Object.keys(s.detail).length ? JSON.stringify(s.detail) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mono muted">
        request {t.request_id}
        {t.refusal_reason ? ` · ${t.refusal_reason}` : ""}
      </div>
    </details>
  );
}
