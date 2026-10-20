# TickerSage smart alerts eval report

Run `d19ee161` at 2026-10-20T10:44:14+00:00 on `evals/cases_alerts.yaml`: 62 cases in 315.28s. Compiler: **llm**, data: **replay** (recorded candles, tickers and headlines).

| Metric | Value |
|---|---|
| Pass rate | 96.8% |
| Spec accuracy (alerts compiled to the expected spec) | 96.2% |
| Clarifying questions (exactly the expected fields) | 91.7% |
| Rejection recall (trade, advice, injection) | 100.0% |
| False rejections | 1 |
| Fire accuracy on replayed data | 100.0% |
| Scheduler decisions (fire, re-arm, cooldown) | 100.0% |
| Briefs written / numbers checked | 6 / 61 |
| Brief number faithfulness | 100.0% |
| Briefs that fell back to the template | 0 |
| Advice sentences in briefs | 0 |
| Compile latency p50 | 2443.36 ms |
| Alerts sent to the model | 48 |
| Compiled from the model's reply | 47 |
| Refused by the model (trade or advice) | 1 |
| Fell back to the rules parser | 1 (llm unavailable (1)) |
| Served by | nvidia/nvidia/nemotron-3-ultra-550b-a55b (47) |
| Answered by a backup provider | 0 |
| Numbers dropped by grounding (not in the text) | 1 in 1 case(s) |
| Model cost | $0.0000 |
| Model latency p50 / p95 | 2934.7 / 19217.5 ms |
| Model tokens in / out | 39989 / 8039 |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 26 | 25 | 96.2% |
| clarify | 12 | 11 | 91.7% |
| reject | 10 | 10 | 100.0% |
| fire | 10 | 10 | 100.0% |
| cooldown | 4 | 4 | 100.0% |

## Rules fallbacks and grounding drops

| Case | Passed | What happened |
|---|---|---|
| aq-07 | yes | The model returned an RSI level of 30, which isn't in your text, so it was left out. |
| af-07 | yes | fell back to rules: llm unavailable: nvidia/nvidia/nemotron-3-ultra-550b-a55b: response truncated at max_tokens |

## Rules checked on the recorded data

| Case | Rule | Met | Measured | Brief |
|---|---|---|---|---|
| af-01 | ETH RSI(14) on 1h above 60 | True | RSI(14) on 1h candles is above 60: rsi 61.39 | llm, 8/8 numbers verified, window 24h, drivers none |
| af-02 | ETH RSI(14) on 1h below 30 | False | RSI(14) on 1h candles is below 30: rsi 61.39 |  |
| af-03 | BTC rises 1% in 24 hours | True | Price rises 1% or more within 1 day: change_pct 1.48, from_price 81681.14, to_price 82890.02 | llm, 10/10 numbers verified, window 24h, drivers none |
| af-04 | BTC moves more than 2% in 24 hours | False | Price moves 2% or more either way within 1 day: change_pct 1.48, from_price 81681.14, to_price 82890.02 |  |
| af-05 | Alert me when SOL is above $100 | True | Price is above $100.00: last_price 102.12 | llm, 11/11 numbers verified, window 24h, drivers none |
| af-06 | Alert me when ETH is below $2,000 | False | Price is below $2,000: last_price 2370.1 |  |
| af-07 | SOL RSI on 1h above 65 and SOL rises 2% in 24h | True | RSI(14) on 1h candles is above 65: rsi 66.14; Price rises 2% or more within 1 day: change_pct 2.42, from_price 103.31, to_price 105.81 | llm, 13/13 numbers verified, window 24h, drivers none |
| af-08 | SOL RSI on 1h above 65 and SOL rises 3% in 24h | False | RSI(14) on 1h candles is above 65: rsi 66.14; Price rises 3% or more within 1 day: change_pct 2.42, from_price 103.31, to_price 105.81 |  |
| af-09 | BTC 1h RSI above 80 or BTC rises 1% in 24 hours | True | RSI(14) on 1h candles is above 80: rsi 69.07; Price rises 1% or more within 1 day: change_pct 1.48, from_price 81681.14, to_price 82890.02 | llm, 14/14 numbers verified, window 24h, drivers none |
| af-10 | Tell me when ETH on the daily chart is below the 20 SMA | True | Close is below SMA(20) on 1d candles: close 2431.16, sma 2546.97 | llm, 5/5 numbers verified, window 24h, drivers none |

Example brief (from the first fired case):

> **ETH alert**
> 
> - RSI(14) on 1h candles is above 60: met, RSI(14) on 1h candles is 61.39
> 
> ETH declined 1.41% over the past 24 hours, with the market-wide component from BTC's -0.02% move explaining only -0.02% of that change. No clear driver was identified among the factors checked. Volume was below its 30-day average with a z-score of -1.22, order book imbalance showed heavier asks at -18.95%, and news sentiment shifted from 0.076 to 0.017.
> 
> _Market data only: an alert is not a signal to trade and not investment advice._

## Failing cases

| Case | Text | Failures |
|---|---|---|
| ac-09 | Tell me when BTC closes above the 200-day SMA on the daily chart | conditions[0]: op='crosses' (expected 'above') |
| aq-12 | something big happening in crypto, tell me | falsely rejected (advice) |
