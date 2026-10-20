# TickerSage eval report

Run `75e24067` at 2026-10-20T06:11:56+00:00: 110 cases in 1.83s. LLM: **offline** (no model calls), tools: **replay**, routing: routed, planner: rules, verifier: on, cache: on.

| Metric | Value |
|---|---|
| Pass rate | 100.0% |
| Tool selection accuracy | 100.0% |
| Intent accuracy | 100.0% |
| Number faithfulness (1119 numbers checked) | 100.0% |
| Key numbers present | 100.0% |
| Refusal accuracy (all cases) | 100.0% |
| Refusal recall (advice, trade, direct injection) | 100.0% |
| False refusal rate | 0.0% |
| Injection resistance (direct + indirect) | 100.0% |
| Concept citation rate | 100.0% |
| No unsupported cause (why-it-moved answers) | 100.0% |
| Latency p50 / p95 | 47.3 ms / 174.1 ms |
| Avg cost per query | $0.000000 |
| Avg tokens in / out | 0.0 / 0.0 |
| Provider fallbacks | 0 |
| LLM judge score | not run |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| price_lookup | 20 | 20 | 100.0% |
| comparison | 15 | 15 | 100.0% |
| explain_move | 15 | 15 | 100.0% |
| concept | 15 | 15 | 100.0% |
| advice | 10 | 10 | 100.0% |
| trade | 5 | 5 | 100.0% |
| direct_injection | 10 | 10 | 100.0% |
| indirect_injection | 5 | 5 | 100.0% |
| broken_tools | 10 | 10 | 100.0% |
| out_of_scope | 5 | 5 | 100.0% |

## Failure taxonomy

No failures.
