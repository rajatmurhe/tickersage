# TickerSage research report evals

Run `cfd16504` at 2026-10-20T07:43:12+00:00: 15 cases in 1.15s. LLM: **offline** (no model calls), tools: **replay**, verifier: on, provider fallback: on.

| Metric | Value |
|---|---|
| Pass rate | 100.0% |
| Section coverage (summary, per-asset sections, comparison, risks, sources, charts) | 100.0% |
| Reports with every section | 100.0% |
| Number faithfulness (1127 numbers checked) | 100.0% |
| Key numbers in each asset's section (48 checked) | 100.0% |
| Refusal recall (advice, trade, injection report requests) | 100.0% |
| False refusal rate | 0.0% |
| Injection resistance | 100.0% |
| Policy block rate | 100.0% |
| LLM-written parts kept after verification | 0.0% |
| Verifier retries / template fallbacks | 0 / 0 |
| Report latency p50 / p95 | 261.8 ms / 338.9 ms |
| Avg LLM calls per report | 0.0 |
| Avg tokens in / out per report | 0.0 / 0.0 |
| Provider fallbacks | 0 |
| LLM judge score | not run |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| report | 8 | 8 | 100.0% |
| advice | 2 | 2 | 100.0% |
| trade | 1 | 1 | 100.0% |
| direct_injection | 1 | 1 | 100.0% |
| broken_tools | 1 | 1 | 100.0% |
| indirect_injection | 1 | 1 | 100.0% |
| policy_scope | 1 | 1 | 100.0% |

## Failure taxonomy

No failures.
