# TickerSage smart alerts eval report

Run `b974c57a` at 2026-10-20T07:50:29+00:00 on `evals/cases_alerts_holdout.yaml`: 30 cases in 0.14s. Compiler: **rules**, data: **replay** (recorded candles, tickers and headlines).

| Metric | Value |
|---|---|
| Pass rate | 96.7% |
| Spec accuracy (alerts compiled to the expected spec) | 100.0% |
| Clarifying questions (exactly the expected fields) | 85.7% |
| Rejection recall (trade, advice, injection) | 100.0% |
| False rejections | 0 |
| Fire accuracy on replayed data | n/a |
| Scheduler decisions (fire, re-arm, cooldown) | n/a |
| Briefs written / numbers checked | 0 / 0 |
| Brief number faithfulness | n/a |
| Briefs that fell back to the template | 0 |
| Advice sentences in briefs | 0 |
| Compile latency p50 | 1.29 ms |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 17 | 17 | 100.0% |
| clarify | 7 | 6 | 85.7% |
| reject | 6 | 6 | 100.0% |

## Failing cases

| Case | Text | Failures |
|---|---|---|
| hc-20 | Tell me when BTC or ETH crosses 50 RSI on 1h | asked about ['symbol', 'threshold'], expected ['symbol'] |
