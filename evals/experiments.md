# TickerSage experiments

Generated 2026-10-18 16:35 UTC.

## routing

Skipped: needs ANTHROPIC_API_KEY.

## verifier

Skipped: needs an LLM API key.

## 3. Market-data cache on vs off

24 price, comparison and explain-move questions against live public APIs, asked twice in a row. Timings are for the second pass (LLM offline, so this isolates tool latency).

| Cache | p50 ms | p95 ms | mean ms |
|---|---|---|---|
| on | 5.0 | 6.4 | 5.2 |
| off | 120.5 | 315.8 | 160.9 |

Median speed-up with the cache warm: 24x.

## providers

Skipped: needs ANTHROPIC_API_KEY and OPENAI_API_KEY.
