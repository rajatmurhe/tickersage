# TickerSage smart alerts eval report

_Smart alerts held-out set (`cases_alerts_holdout.yaml`, 30 cases), first run on Nemotron 3 Ultra, 2026-10-20: Nemotron 3 Ultra (nvidia/nemotron-3-ultra-550b-a55b) on NVIDIA's free tier, strict JSON schema, thinking off, no other provider configured, at commit 9136447. Nemotron 3 Super, the repo's default NVIDIA model, answered HTTP 404 for this account on 2026-10-20, so these runs use Ultra. It is this model's first run on the set, but the set is no longer blind to the compiler: the reply reconciliation was added after reading the gpt-oss-20b failures on it (history/26). The one failure is "BTC above 95k": the model left the price null, so the compiler asked for it._

Run `255eccab` at 2026-10-20T10:45:56+00:00 on `evals/cases_alerts_holdout.yaml`: 30 cases in 91.63s. Compiler: **llm**, data: **replay** (recorded candles, tickers and headlines).

| Metric | Value |
|---|---|
| Pass rate | 96.7% |
| Spec accuracy (alerts compiled to the expected spec) | 94.1% |
| Clarifying questions (exactly the expected fields) | 100.0% |
| Rejection recall (trade, advice, injection) | 100.0% |
| False rejections | 0 |
| Fire accuracy on replayed data | n/a |
| Scheduler decisions (fire, re-arm, cooldown) | n/a |
| Briefs written / numbers checked | 0 / 0 |
| Brief number faithfulness | n/a |
| Briefs that fell back to the template | 0 |
| Advice sentences in briefs | 0 |
| Compile latency p50 | 2357.91 ms |
| Alerts sent to the model | 24 |
| Compiled from the model's reply | 24 |
| Refused by the model (trade or advice) | 0 |
| Fell back to the rules parser | 0 |
| Served by | nvidia/nvidia/nemotron-3-ultra-550b-a55b (24) |
| Answered by a backup provider | 0 |
| Numbers dropped by grounding (not in the text) | 0 in 0 case(s) |
| Model cost | $0.0000 |
| Model latency p50 / p95 | 2949.5 / 12478.2 ms |
| Model tokens in / out | 18072 / 3647 |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 17 | 16 | 94.1% |
| clarify | 7 | 7 | 100.0% |
| reject | 6 | 6 | 100.0% |

## Failing cases

| Case | Text | Failures |
|---|---|---|
| hc-03 | BTC above 95k, tell me | status needs_clarification, asked about ['threshold'] |
