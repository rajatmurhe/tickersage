# TickerSage Strategy Studio eval report

_Strategy Studio dev set (`cases_strategy.yaml`, 72 cases), first run of the LLM compiler against a live model, 2026-10-19: Nemotron 3 Super via NVIDIA's free tier, strict JSON schema, low effort (3 calls timed out on NVIDIA and were answered by Groq's gpt-oss-20b). Nothing in the compiler, prompt, linter or cases was changed before this run. 13 cases never reach the model (5 pre-built broken specs, 8 refused inputs). Latency was measured on a heavily loaded machine (load average ~30, swap nearly full)._

Run `137f11b0` at 2026-10-19T07:45:35+00:00: 72 cases in 325.96s. Compiler: **llm**, candles: **replay** (Binance klines, 500 per backtest).

| Metric | Value |
|---|---|
| Pass rate | 66.7% |
| Spec accuracy (ideas compiled to the expected spec) | 40.9% |
| Clarifying questions (exactly the expected fields) | 56.2% |
| Linter catch rate (deliberately broken ideas) | 90.9% |
| Rejection recall (injection, live execution) | 100.0% |
| False rejections | 0 |
| Error findings on well-formed ideas | 0 |
| Backtests completed with consistent numbers | 50.0% |
| Compile latency p50 | 4141.07 ms |
| Ideas sent to the model | 59 |
| Compiled from the model's reply | 59 |
| Fell back to the rules parser | 0 |
| Served by | nvidia/nvidia/nemotron-3-super-120b-a12b (56), groq/openai/gpt-oss-20b (3) |
| Answered by a backup provider | 3 |
| Numbers dropped by grounding (not in the idea) | 1 in 1 case(s) |
| Model cost | $0.0000 |
| Model latency p50 / p95 | 4330.8 / 7695.9 ms |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 22 | 9 | 40.9% |
| clarify | 16 | 9 | 56.2% |
| broken | 22 | 20 | 90.9% |
| reject | 8 | 8 | 100.0% |
| backtest | 4 | 2 | 50.0% |

## Rules fallbacks and grounding drops

| Case | Passed | What happened |
|---|---|---|
| sb-12 | no | The model returned an RSI threshold of -10, which isn't in your text, so it was left out. |

## Backtests on the recorded candles

How the rules behaved on one recorded window of history. Not a forecast and not investment advice.

| Case | Asset | Window | Trades | Total return | Buy and hold | Max drawdown | Win rate | Avg trade | Exposure |
|---|---|---|---|---|---|---|---|---|---|
| st-02 | BTC 1d | 2025-12-24 to 2026-10-18 | 1 | 0.87% | -6.62% | -1.28% | 100.0% | 4.35% | 13.42% |
| st-04 | ETH 1h | 2026-10-06 to 2026-10-19 | 0 | 0.0% | -9.97% | 0.0% | n/a | n/a | 0.0% |

## Failing cases

| Case | Idea | Failures |
|---|---|---|
| sc-01 | Buy ETH on the 4h chart when RSI(14) drops below 30. Take profit at 6%, stop loss at 3%, and use 10% of the demo balance per trade. | status needs_clarification, asked about ['lookback'] |
| sc-03 | SOL 1h: buy when price falls 4% within 6 hours. Sell at +3% or stop out at -2%. Max hold 24 hours. Size 15%. | status needs_clarification, asked about ['lookback'] |
| sc-04 | Buy BTC when RSI is oversold on hourly candles; take profit 2%, stop loss 1%, position size 5% | status needs_clarification, asked about ['lookback'] |
| sc-06 | ETH daily - enter when RSI crosses above 30, sell when RSI goes above 70, stop loss 5%, allocate 25%. | exit_signals[0]: op='above' (expected 'crosses_above') |
| sc-07 | Buy SOL on 15 minute candles when price drops 2% in 1 hour, take profit 1.5%, stop loss 1%, use $500 per trade | status needs_clarification, asked about ['entry', 'position_size'] |
| sc-09 | BTC 4h: buy when RSI(7) dips below 20 or price drops 6% in 24 hours. TP 4%, SL 3%, 10% size. | entry[0]: op='below' (expected 'crosses_below') |
| sc-11 | buy btc on daily candles when price closes below the 20 sma, take profit 7%, stop loss 4%, 10% position, fees 0.075% | status needs_clarification, asked about ['lookback'] |
| sc-12 | ETH 1h, buy when RSI is below 25, take profit at 3 percent, stop at 2 percent, 10 percent of balance, slippage 0.1% | status needs_clarification, asked about ['lookback'] |
| sc-14 | When BTC's RSI on the 4h falls under 30, buy. Exit at a 5% gain or a 2% loss. Size: 10%. | status needs_clarification, asked about ['lookback'] |
| sc-15 | Buy ETH on daily candles whenever the price is above its 100-day moving average, sell when it crosses back below, stop loss 6%, 30% of my portfolio | exit_signals[0]: op='below' (expected 'crosses_below') |
| sc-17 | Buy XRP on the 1h when it drops 3% within 4 hours. Take 2% profit. Stop at 1.5%. Use 8% of the balance. | status needs_clarification, asked about ['lookback'] |
| sc-18 | Buy BTC on 4h candles when RSI crosses below 25; take profit +6%; stop -3%; position size 12% | status needs_clarification, asked about ['lookback'] |
| sc-21 | buy ETH when RSI drops below 30, take profit at 5%, stop at 3% | status needs_clarification, asked about ['lookback'] |
| sq-02 | Buy BTC on 4h when RSI < 30, take profit 5%, 10% size | asked about ['lookback', 'stop_loss'], expected ['stop_loss'] |
| sq-03 | Buy SOL when the 20 SMA crosses above the 50 SMA | asked about ['interval', 'entry', 'stop_loss', 'exit', 'position_size'], expected ['interval', 'stop_loss', 'exit', 'position_size'] |
| sq-05 | Buy ETH and BTC on 1h when RSI < 30, tp 5% sl 3%, 10% per trade | asked about ['symbol', 'entry', 'position_size'], expected ['symbol'] |
| sq-08 | Buy ETH on 4h when the 9 EMA crosses the 21 EMA, take profit 5%, stop 2%, 10% size | asked about ['entry', 'lookback'], expected ['entry'] |
| sq-10 | Buy ETH on 1h when RSI < 30 with 10x leverage, take profit 5%, stop 2% | asked about ['lookback', 'position_size'], expected ['position_size'] |
| sq-12 | Buy DOGE on 1h when RSI < 30, take profit 5%, 3% trailing stop, 10% size | asked about ['lookback', 'stop_loss'], expected ['stop_loss'] |
| sq-16 | Buy ETH on 4h when RSI < 30 and MACD crosses up, tp 5% sl 2% 10% size | asked about ['entry', 'lookback'], expected ['entry'] |
| sb-01 | Buy ETH on 4h when RSI < 30, take profit 5%, no stop loss, 10% size | linter missed ['no_stop_loss'] (raised []) |
| sb-12 | Buy ETH on 4h when RSI < 30, sell when RSI drops below -10, stop 3%, 10% size | linter missed ['rsi_out_of_range'] (raised []) |
| st-01 | Buy ETH on the 4h chart when RSI(14) drops below 30. Take profit at 5%, stop loss at 3%, 10% of the demo balance. | didn't compile: ['lookback'] |
| st-03 | SOL 1h: buy when price falls 4% within 6 hours. Sell at +3% or stop out at -2%. Max hold 24 hours. Size 15%. | didn't compile: ['lookback'] |
