# TickerSage Strategy Studio eval report

Run `e82d70f1` at 2026-10-19T07:48:53+00:00: 40 cases in 170.16s. Compiler: **llm**, candles: **replay** (Binance klines, 500 per backtest).

| Metric | Value |
|---|---|
| Pass rate | 62.5% |
| Spec accuracy (ideas compiled to the expected spec) | 60.0% |
| Clarifying questions (exactly the expected fields) | 40.0% |
| Linter catch rate (deliberately broken ideas) | 70.0% |
| Rejection recall (injection, live execution) | 100.0% |
| False rejections | 0 |
| Error findings on well-formed ideas | 0 |
| Backtests completed with consistent numbers | n/a |
| Compile latency p50 | 4059.29 ms |
| Ideas sent to the model | 35 |
| Compiled from the model's reply | 35 |
| Fell back to the rules parser | 0 |
| Served by | nvidia/nvidia/nemotron-3-super-120b-a12b (35) |
| Answered by a backup provider | 0 |
| Numbers dropped by grounding (not in the idea) | 0 in 0 case(s) |
| Model cost | $0.0000 |
| Model latency p50 / p95 | 4130.8 / 10376.8 ms |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 15 | 9 | 60.0% |
| clarify | 10 | 4 | 40.0% |
| broken | 10 | 7 | 70.0% |
| reject | 5 | 5 | 100.0% |

## Failing cases

| Case | Idea | Failures |
|---|---|---|
| hc-01 | buy ETH on the 4h when RSI drops below 30. tp 6% sl 3%, $1000 per trade | status needs_clarification, asked about ['position_size'] |
| hc-03 | sol 1h: buy when rsi(14) is under 25, sell when rsi goes above 60. stop loss 2%, use 5% of my balance | exit_signals[0]: op='above' (expected 'crosses_above') |
| hc-04 | Buy DOGE when price drops 8% in 24 hours on 1h candles, take profit 5%, stop loss 4%, $500 per trade | status needs_clarification, asked about ['entry', 'position_size'] |
| hc-06 | avax, 15m chart. buy if RSI < 20. stoploss 1.5%, take profit 3%, exit after 12 hours, position size $1500 | status needs_clarification, asked about ['position_size'] |
| hc-10 | Buy ADA when it rises 10% in 48 hours (4h chart). Take profit at 12%, stop loss at 6%, $2000 per trade | status needs_clarification, asked about ['entry', 'lookback', 'position_size'] |
| hc-13 | buy sol when rsi drops below 25, take profit 10%, stop loss 5%, $1000 per trade | status needs_clarification, asked about ['lookback', 'position_size'] |
| hq-02 | BTC 4h, buy when price drops 5%, tp 8% sl 4%, $1000 per trade | asked about ['lookback', 'position_size'], expected ['lookback'] |
| hq-03 | buy when rsi is oversold, take profit 5%, stop loss 2%, $1000 per trade | asked about ['symbol', 'interval', 'entry', 'lookback', 'position_size'], expected ['symbol', 'interval'] |
| hq-04 | BTC and ETH on 4h, buy when rsi crosses below 30, tp 6% sl 3%, 10% position size | asked about ['symbol', 'lookback'], expected ['symbol'] |
| hq-06 | DOGE daily, buy when price is above the 50 sma, tp 20%, trailing stop 5%, $1000 per trade | asked about ['entry', 'stop_loss', 'position_size'], expected ['stop_loss'] |
| hq-08 | short XRP if it breaks below $0.50 on the 4h, tp 10% sl 5% size 10% | asked about ['entry', 'lookback'], expected ['entry'] |
| hq-10 | ETH 4h. buy when RSI drops below 30. tp 8%, sl 4%, 10% | asked about ['lookback'], expected ['position_size'] |
| hb-03 | SOL 1h buy when rsi is below 25, tp 5%, sl 2%, $6000 per trade | linter missed ['size_above_cap'] (raised []) |
| hb-09 | ETH daily candles, buy when price drops 6% in 8 hours, take profit 8%, stop loss 4%, $1000 per trade | linter missed ['timeframe_mismatch'] (raised []) |
| hb-10 | XRP 4h buy when rsi crosses below 30, tp 6% sl 3%, $50 per trade | linter missed ['suspicious_decimal'] (raised []) |
