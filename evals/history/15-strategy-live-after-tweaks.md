# TickerSage Strategy Studio eval report

_Strategy Studio dev set (`cases_strategy.yaml`, 72 cases), live LLM compiler after the fixes from the first live runs, 2026-10-19: same model and settings as history 13 (Nemotron 3 Super via NVIDIA's free tier, strict JSON schema, low effort; 1 call answered by Groq's gpt-oss-20b after an NVIDIA timeout). **This is a training score:** the prompt changes and `_fold_windowless_exits` were written by looking at this set's first-run failures (and the held-out set's). The intermediate run `6b9d6f35`, after the first round of fixes, passed 88.9%. Replaying this run's recorded model replies through the committed compiler gives the same result for all 72 cases. Latency was measured on a heavily loaded machine._

Run `3e549052` at 2026-10-19T13:40:30+00:00: 72 cases in 244.04s. Compiler: **llm**, candles: **replay** (Binance klines, 500 per backtest).

| Metric | Value |
|---|---|
| Pass rate | 90.3% |
| Spec accuracy (ideas compiled to the expected spec) | 81.8% |
| Clarifying questions (exactly the expected fields) | 93.8% |
| Linter catch rate (deliberately broken ideas) | 90.9% |
| Rejection recall (injection, live execution) | 100.0% |
| False rejections | 0 |
| Error findings on well-formed ideas | 0 |
| Backtests completed with consistent numbers | 100.0% |
| Compile latency p50 | 2919.7 ms |
| Ideas sent to the model | 59 |
| Compiled from the model's reply | 59 |
| Fell back to the rules parser | 0 |
| Served by | nvidia/nvidia/nemotron-3-super-120b-a12b (58), groq/openai/gpt-oss-20b (1) |
| Answered by a backup provider | 1 |
| Numbers dropped by grounding (not in the idea) | 0 in 0 case(s) |
| Model cost | $0.0000 |
| Model latency p50 / p95 | 3150.2 / 7417.5 ms |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 22 | 18 | 81.8% |
| clarify | 16 | 15 | 93.8% |
| broken | 22 | 20 | 90.9% |
| reject | 8 | 8 | 100.0% |
| backtest | 4 | 4 | 100.0% |

## Backtests on the recorded candles

How the rules behaved on one recorded window of history. Not a forecast and not investment advice.

| Case | Asset | Window | Trades | Total return | Buy and hold | Max drawdown | Win rate | Avg trade | Exposure |
|---|---|---|---|---|---|---|---|---|---|
| st-01 | ETH 4h | 2026-07-30 to 2026-10-19 | 2 | 0.15% | 25.97% | -0.33% | 50.0% | 0.75% | 3.31% |
| st-02 | BTC 1d | 2025-12-24 to 2026-10-18 | 1 | 0.87% | -6.62% | -1.28% | 100.0% | 4.35% | 13.42% |
| st-03 | SOL 1h | 2026-09-28 to 2026-10-19 | 3 | 0.49% | -12.12% | -0.34% | 66.67% | 1.08% | 1.42% |
| st-04 | ETH 1h | 2026-10-06 to 2026-10-19 | 0 | 0.0% | -9.97% | 0.0% | n/a | n/a | 0.0% |

## Failing cases

| Case | Idea | Failures |
|---|---|---|
| sc-06 | ETH daily - enter when RSI crosses above 30, sell when RSI goes above 70, stop loss 5%, allocate 25%. | exit_signals[0]: op='above' (expected 'crosses_above') |
| sc-07 | Buy SOL on 15 minute candles when price drops 2% in 1 hour, take profit 1.5%, stop loss 1%, use $500 per trade | status needs_clarification, asked about ['position_size'] |
| sc-14 | When BTC's RSI on the 4h falls under 30, buy. Exit at a 5% gain or a 2% loss. Size: 10%. | entry[0]: op='below' (expected 'crosses_below') |
| sc-15 | Buy ETH on daily candles whenever the price is above its 100-day moving average, sell when it crosses back below, stop loss 6%, 30% of my portfolio | exit_signals[0]: op='below' (expected 'crosses_below') |
| sq-03 | Buy SOL when the 20 SMA crosses above the 50 SMA | asked about ['interval', 'entry', 'stop_loss', 'exit', 'position_size'], expected ['interval', 'stop_loss', 'exit', 'position_size'] |
| sb-17 | Buy ETH on 4h when RSI is below 30 and RSI is above 70, tp 5% sl 3% 10% size | linter missed ['entry_never_triggers'] (raised []) |
| sb-20 | Buy ETH on daily when the 50 SMA crosses above the 50 SMA, tp 5% sl 3% 10% size | linter missed ['entry_never_triggers'] (raised []) |
