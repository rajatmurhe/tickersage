# TickerSage eval report

Run `23016016` at 2026-10-20T19:28:25+00:00: 34 cases in 0.28s. LLM: **offline** (no model calls), tools: **replay**, routing: routed, planner: rules, verifier: on, cache: on.

| Metric | Value |
|---|---|
| Pass rate | 100.0% |
| Tool selection accuracy | 100.0% |
| Intent accuracy | 100.0% |
| Number faithfulness (199 numbers checked) | 100.0% |
| Key numbers present | n/a |
| Refusal accuracy (all cases) | 100.0% |
| Refusal recall (advice, trade, direct injection) | 100.0% |
| False refusal rate | 0.0% |
| Injection resistance (direct + indirect) | n/a |
| Concept citation rate | n/a |
| No unsupported cause (why-it-moved answers) | n/a |
| Latency p50 / p95 | 22.0 ms / 41.1 ms |
| Avg cost per query | $0.000000 |
| Avg tokens in / out | 0.0 / 0.0 |
| Provider fallbacks | 0 |
| LLM judge score | not run |
| Policy block rate (out-of-scope and over-limit calls denied, none ran) | 100.0% |
| Secret redaction rate (no trace of the secret anywhere, user warned) | 100.0% |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| policy_scope | 10 | 10 | 100.0% |
| policy_limits | 10 | 10 | 100.0% |
| secret_paste | 12 | 12 | 100.0% |
| secret_lookalike | 2 | 2 | 100.0% |

## Failure taxonomy

No failures.
