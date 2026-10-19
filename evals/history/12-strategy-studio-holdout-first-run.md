# TickerSage Strategy Studio eval report

_Strategy Studio held-out set, first run of the rules compiler, 2026-10-19. A separate agent wrote the 40 cases blind, from a feature description that listed the supported phrasings, without seeing the parser, the linter or the dev set. Nothing was tuned on it. The dev set (`cases_strategy.yaml`, 72 cases, written by the parser's author) also passed 72/72 on its first run._

Run `edc602bb` at 2026-10-19T07:16:44+00:00: 40 cases in 0.1s. Compiler: **rules**, candles: **replay** (Binance klines, 500 per backtest).

| Metric | Value |
|---|---|
| Pass rate | 100.0% |
| Spec accuracy (ideas compiled to the expected spec) | 100.0% |
| Clarifying questions (exactly the expected fields) | 100.0% |
| Linter catch rate (deliberately broken ideas) | 100.0% |
| Rejection recall (injection, live execution) | 100.0% |
| False rejections | 0 |
| Error findings on well-formed ideas | 0 |
| Backtests completed with consistent numbers | n/a |
| Compile latency p50 | 1.6 ms |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 15 | 15 | 100.0% |
| clarify | 10 | 10 | 100.0% |
| broken | 10 | 10 | 100.0% |
| reject | 5 | 5 | 100.0% |

## Failing cases

No failures.
