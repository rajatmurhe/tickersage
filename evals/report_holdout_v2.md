# TickerSage eval report

Run `18069c13` at 2026-10-20T06:12:12+00:00: 30 cases in 0.38s. LLM: **offline** (no model calls), tools: **replay**, routing: routed, planner: rules, verifier: on, cache: on.

| Metric | Value |
|---|---|
| Pass rate | 100.0% |
| Tool selection accuracy | 100.0% |
| Intent accuracy | 100.0% |
| Number faithfulness (255 numbers checked) | 100.0% |
| Key numbers present | 100.0% |
| Refusal accuracy (all cases) | 100.0% |
| Refusal recall (advice, trade, direct injection) | 100.0% |
| False refusal rate | 0.0% |
| Injection resistance (direct + indirect) | 100.0% |
| Concept citation rate | 100.0% |
| No unsupported cause (why-it-moved answers) | 100.0% |
| Latency p50 / p95 | 29.0 ms / 159.3 ms |
| Avg cost per query | $0.000000 |
| Avg tokens in / out | 0.0 / 0.0 |
| Provider fallbacks | 0 |
| LLM judge score | not run |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| price_lookup | 5 | 5 | 100.0% |
| comparison | 3 | 3 | 100.0% |
| explain_move | 3 | 3 | 100.0% |
| concept | 5 | 5 | 100.0% |
| advice | 4 | 4 | 100.0% |
| trade | 2 | 2 | 100.0% |
| direct_injection | 2 | 2 | 100.0% |
| indirect_injection | 1 | 1 | 100.0% |
| broken_tools | 2 | 2 | 100.0% |
| out_of_scope | 3 | 3 | 100.0% |

## Failure taxonomy

No failures.
