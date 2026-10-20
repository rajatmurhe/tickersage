# TickerSage Strategy Studio eval report

Run `18d13b59` at 2026-10-20T06:12:20+00:00: 72 cases in 0.19s. Compiler: **rules**, candles: **replay** (Binance klines, 500 per backtest).

| Metric | Value |
|---|---|
| Pass rate | 100.0% |
| Spec accuracy (ideas compiled to the expected spec) | 100.0% |
| Clarifying questions (exactly the expected fields) | 100.0% |
| Linter catch rate (deliberately broken ideas) | 100.0% |
| Rejection recall (injection, live execution) | 100.0% |
| False rejections | 0 |
| Error findings on well-formed ideas | 0 |
| Backtests completed with consistent numbers | 100.0% |
| Compile latency p50 | 1.2 ms |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 22 | 22 | 100.0% |
| clarify | 16 | 16 | 100.0% |
| broken | 22 | 22 | 100.0% |
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

No failures.
