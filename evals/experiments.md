# TickerSage experiments

Updated 2026-10-20 UTC.

<!-- exp:routing -->
## 1. Routed vs single strong model

Held-out v4 (33 cases) on Groq's free tier. The routed row is v4's first live run; the single row ran right after it on the same cases. Groq's free tier caps each model at 200K tokens a day, which is why this uses v4 rather than the 110-case set. One difference: the single run already had the fix for space-grouped thousands (failure 9 in the README), which only changes how many drafts the verifier sends back.

33 cases, provider `groq`, provider fallback off, judge: nvidia/nemotron-3-super-120b-a12b. Run 2026-10-20T05:45:57+00:00.

| Config | Pass rate | Faithfulness | Judge | p50 ms | p95 ms | Cost / query | Models |
|---|---|---|---|---|---|---|---|
| routed (gpt-oss-20b routes, 120b writes) | 78.8% | 100.0% | 87.9% | 729.9 | 1679.7 | $0.00000 | openai/gpt-oss-120b, openai/gpt-oss-20b |
| single (gpt-oss-120b everywhere) | 78.8% | 100.0% | 88.5% | 1090.2 | 4503.1 | $0.00000 | openai/gpt-oss-120b |

<!-- exp:verifier -->
## 2. Number verifier on vs off

110 cases, provider `nvidia`, provider fallback off, judge: nvidia/nemotron-3-super-120b-a12b. Run 2026-10-20T05:43:17+00:00.

| Config | Pass rate | Faithfulness | Judge | p50 ms | p95 ms | Cost / query | Models |
|---|---|---|---|---|---|---|---|
| verifier on | 80.0% | 100.0% | 95.3% | 2222.3 | 7700.9 | $0.00000 | nvidia/nemotron-3-super-120b-a12b |
| verifier off | 78.2% | 99.7% | 95.5% | 2339.2 | 11011.9 | $0.00000 | nvidia/nemotron-3-super-120b-a12b |

With the verifier off, 1 of 318 numbers that reached users was unsupported: explain-12 said Ethereum "underperformed by about 3.7 percentage points", a subtraction the model did itself (6.58 − 2.9; no tool returned it). With the verifier on, that draft was rejected and the user got the deterministic figures.

<!-- exp:cache -->
## 3. Market-data cache on vs off

24 price, comparison and explain-move questions against live public APIs, asked twice in a row. Timings are for the second pass (LLM offline, so this isolates tool latency).

| Cache | p50 ms | p95 ms | mean ms |
|---|---|---|---|
| on | 5.0 | 6.4 | 5.2 |
| off | 120.5 | 315.8 | 160.9 |

Median speed-up with the cache warm: 24x.

<!-- exp:providers -->
## providers

Skipped: needs ANTHROPIC_API_KEY and OPENAI_API_KEY.
