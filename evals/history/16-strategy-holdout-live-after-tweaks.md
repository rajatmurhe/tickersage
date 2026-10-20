# TickerSage Strategy Studio eval report

_Strategy Studio held-out set (`cases_strategy_holdout.yaml`, 40 cases), second live run, after the same fixes as history 15, 2026-10-19: Nemotron 3 Super via NVIDIA (all 35 calls). **The set is no longer blind:** its first live run (history 14, 62.5%) was read before the fixes, and the dollar-size prompt change mostly targets its failures (11 of its 15 first-run failures had a dollar position size like "$1000 per trade"). Treat 85% as a tuned score; history 14 is the honest number. Replaying this run's recorded model replies through the committed compiler gives the same result for all 40 cases._

Run `1189655d` at 2026-10-19T13:43:30+00:00: 40 cases in 154.84s. Compiler: **llm**, candles: **replay** (Binance klines, 500 per backtest).

| Metric | Value |
|---|---|
| Pass rate | 85.0% |
| Spec accuracy (ideas compiled to the expected spec) | 86.7% |
| Clarifying questions (exactly the expected fields) | 70.0% |
| Linter catch rate (deliberately broken ideas) | 90.0% |
| Rejection recall (injection, live execution) | 100.0% |
| False rejections | 0 |
| Error findings on well-formed ideas | 0 |
| Backtests completed with consistent numbers | n/a |
| Compile latency p50 | 3302.41 ms |
| Ideas sent to the model | 35 |
| Compiled from the model's reply | 35 |
| Fell back to the rules parser | 0 |
| Served by | nvidia/nvidia/nemotron-3-super-120b-a12b (35) |
| Answered by a backup provider | 0 |
| Numbers dropped by grounding (not in the idea) | 0 in 0 case(s) |
| Model cost | $0.0000 |
| Model latency p50 / p95 | 3310.8 / 9626.7 ms |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 15 | 13 | 86.7% |
| clarify | 10 | 7 | 70.0% |
| broken | 10 | 9 | 90.0% |
| reject | 5 | 5 | 100.0% |

## Failing cases

| Case | Idea | Failures |
|---|---|---|
| hc-03 | sol 1h: buy when rsi(14) is under 25, sell when rsi goes above 60. stop loss 2%, use 5% of my balance | exit_signals[0]: op='above' (expected 'crosses_above') |
| hc-10 | Buy ADA when it rises 10% in 48 hours (4h chart). Take profit at 12%, stop loss at 6%, $2000 per trade | status needs_clarification, asked about ['position_size'] |
| hq-03 | buy when rsi is oversold, take profit 5%, stop loss 2%, $1000 per trade | asked about ['symbol', 'interval', 'position_size'], expected ['symbol', 'interval'] |
| hq-08 | short XRP if it breaks below $0.50 on the 4h, tp 10% sl 5% size 10% | asked about ['symbol', 'entry'], expected ['entry'] |
| hq-10 | ETH 4h. buy when RSI drops below 30. tp 8%, sl 4%, 10% | status ok, expected questions |
| hb-10 | XRP 4h buy when rsi crosses below 30, tp 6% sl 3%, $50 per trade | linter missed ['suspicious_decimal'] (raised []) |
