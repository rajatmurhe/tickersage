# TickerSage smart alerts eval report

Run `2a412c18` at 2026-10-20T07:50:41+00:00 on `evals/cases_alerts.yaml`: 62 cases in 1.32s. Compiler: **rules**, data: **replay** (recorded candles, tickers and headlines).

| Metric | Value |
|---|---|
| Pass rate | 100.0% |
| Spec accuracy (alerts compiled to the expected spec) | 100.0% |
| Clarifying questions (exactly the expected fields) | 100.0% |
| Rejection recall (trade, advice, injection) | 100.0% |
| False rejections | 0 |
| Fire accuracy on replayed data | 100.0% |
| Scheduler decisions (fire, re-arm, cooldown) | 100.0% |
| Briefs written / numbers checked | 6 / 46 |
| Brief number faithfulness | 100.0% |
| Briefs that fell back to the template | 0 |
| Advice sentences in briefs | 0 |
| Compile latency p50 | 1.34 ms |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 26 | 26 | 100.0% |
| clarify | 12 | 12 | 100.0% |
| reject | 10 | 10 | 100.0% |
| fire | 10 | 10 | 100.0% |
| cooldown | 4 | 4 | 100.0% |

## Rules checked on the recorded data

| Case | Rule | Met | Measured | Brief |
|---|---|---|---|---|
| af-01 | ETH RSI(14) on 1h above 60 | True | RSI(14) on 1h candles is above 60: rsi 61.39 | template, 7/7 numbers verified, window 24h, drivers none |
| af-02 | ETH RSI(14) on 1h below 30 | False | RSI(14) on 1h candles is below 30: rsi 61.39 |  |
| af-03 | BTC rises 1% in 24 hours | True | Price rises 1% or more within 1 day: change_pct 1.48, from_price 81681.14, to_price 82890.02 | template, 6/6 numbers verified, window 24h, drivers none |
| af-04 | BTC moves more than 2% in 24 hours | False | Price moves 2% or more either way within 1 day: change_pct 1.48, from_price 81681.14, to_price 82890.02 |  |
| af-05 | Alert me when SOL is above $100 | True | Price is above $100.00: last_price 102.12 | template, 7/7 numbers verified, window 24h, drivers none |
| af-06 | Alert me when ETH is below $2,000 | False | Price is below $2,000: last_price 2370.1 |  |
| af-07 | SOL RSI on 1h above 65 and SOL rises 2% in 24h | True | RSI(14) on 1h candles is above 65: rsi 66.14; Price rises 2% or more within 1 day: change_pct 2.42, from_price 103.31, to_price 105.81 | template, 11/11 numbers verified, window 24h, drivers none |
| af-08 | SOL RSI on 1h above 65 and SOL rises 3% in 24h | False | RSI(14) on 1h candles is above 65: rsi 66.14; Price rises 3% or more within 1 day: change_pct 2.42, from_price 103.31, to_price 105.81 |  |
| af-09 | BTC 1h RSI above 80 or BTC rises 1% in 24 hours | True | RSI(14) on 1h candles is above 80: rsi 69.07; Price rises 1% or more within 1 day: change_pct 1.48, from_price 81681.14, to_price 82890.02 | template, 8/8 numbers verified, window 24h, drivers none |
| af-10 | Tell me when ETH on the daily chart is below the 20 SMA | True | Close is below SMA(20) on 1d candles: close 2431.16, sma 2546.97 | template, 7/7 numbers verified, window 24h, drivers none |

Example brief (from the first fired case):

> **ETH alert**
> 
> - RSI(14) on 1h candles is above 60: met, RSI(14) on 1h candles is 61.39
> 
> **Why it moved**: over the last 24 hours, ETH moved -1.41%. With a 30-day beta of 1.06 to BTC, which moved -0.02%, the market-implied part is -0.02% and the coin-specific residual is -1.39%. No clear driver found: nothing in the evidence crossed its threshold.
> 
> _Market data only: an alert is not a signal to trade and not investment advice._

## Failing cases

No failures.
