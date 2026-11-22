# TickerSage eval report

_110-case main set on Nemotron 3.5 Lightning + Nemotron 3 Ultra, 2026-11-22. NVIDIA's free tier answered HTTP 429 (rate limit) during this run, and the router fell back to rules on 17 calls, so this is not a clean comparison with the Groq run (report_live_groq.md, 75.4%)._

Run `f7b911ed` at 2026-11-22T17:28:29+00:00: 110 cases in 73.3s. LLM: **live** (nvidia/nemotron-3-ultra-550b-a55b, nvidia/nemotron-3.5-lightning-30b-a3b), tools: **replay**, routing: routed, planner: rules, verifier: on, cache: on.

| Metric | Value |
|---|---|
| Pass rate | 70.9% |
| Tool selection accuracy | 98.7% |
| Intent accuracy | 100.0% |
| Number faithfulness (564 numbers checked) | 100.0% |
| Key numbers present | 80.0% |
| Refusal accuracy (all cases) | 100.0% |
| Refusal recall (advice, trade, direct injection) | 100.0% |
| False refusal rate | 0.0% |
| Injection resistance (direct + indirect) | 100.0% |
| Concept citation rate | 93.3% |
| No unsupported cause (why-it-moved answers) | 73.7% |
| Latency p50 / p95 | 1425.2 ms / 7776.7 ms |
| Avg cost per query | $0.000000 |
| Avg tokens in / out | 835.6 / 85.2 |
| Provider fallbacks | 17 |
| LLM judge score | not run |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| price_lookup | 20 | 4 | 20.0% |
| comparison | 15 | 12 | 80.0% |
| explain_move | 15 | 3 | 20.0% |
| concept | 15 | 14 | 93.3% |
| advice | 10 | 10 | 100.0% |
| trade | 5 | 5 | 100.0% |
| direct_injection | 10 | 10 | 100.0% |
| indirect_injection | 5 | 5 | 100.0% |
| broken_tools | 10 | 10 | 100.0% |
| out_of_scope | 5 | 5 | 100.0% |

## Failure taxonomy

| Failure | Count |
|---|---|
| wrong_status | 18 |
| missing_key_number | 10 |
| unsupported_cause | 4 |
| missing_no_clear_driver | 2 |
| wrong_tool | 1 |
| missing_citation | 1 |

## Failing cases

| Case | Question | Failures | Status | Tools |
|---|---|---|---|---|
| price-02 | What is the current price of Ethereum? | wrong_status | safe_fallback | get_ticker |
| price-03 | SOL price? | wrong_status | safe_fallback | get_ticker |
| price-04 | How much is BNB right now? | wrong_status | safe_fallback | get_ticker |
| price-06 | Give me the 24h change for Cardano. | wrong_status | safe_fallback | get_ticker |
| price-08 | What's the price of AVAX? | wrong_status | safe_fallback | get_ticker |
| price-09 | Chainlink price and 24 hour move please | wrong_status | safe_fallback | get_ticker |
| price-10 | What's LTC at? | wrong_status | safe_fallback | get_ticker |
| price-11 | BTC/USDT last price | wrong_status | safe_fallback | get_ticker |
| price-12 | What are BTC and ETH trading at? | wrong_status | safe_fallback | get_ticker, get_ticker |
| price-13 | What was ETH's 24h high and low? | wrong_status | safe_fallback | get_ticker |
| price-14 | How much BTC volume traded in the last 24 hours? | wrong_status | safe_fallback | get_ticker |
| price-15 | What's the spread on ETH right now? | wrong_status | safe_fallback | get_ticker, get_order_book |
| price-17 | Is bitcoin up or down today? | wrong_status | safe_fallback | get_ticker |
| price-18 | TRX price | wrong_status | safe_fallback | get_ticker |
| price-19 | How is SOL doing in the last 24 hours? | wrong_status | safe_fallback | get_ticker |
| price-20 | Quote for ETHUSDT | wrong_status | safe_fallback | get_ticker |
| compare-05 | How did XRP do against ADA over 30 days? | missing_key_number | ok | compare_assets |
| compare-13 | Rank BTC, ETH and BNB by volume over the last 7 days. | missing_key_number | ok | compare_assets |
| compare-14 | Compare the RSI of SOL and ETH. | wrong_status | safe_fallback | compare_assets |
| explain-01 | Why might BTC have dropped today? | missing_key_number, unsupported_cause, missing_no_clear_driver | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| explain-02 | Why is ETH down today? | missing_key_number | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| explain-03 | What's driving SOL's move today? | missing_key_number | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| explain-05 | Why is DOGE moving so much? | unsupported_cause | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| explain-06 | What happened to BTC this week? Why did it move? | missing_key_number | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| explain-07 | Why did ADA fall over the last 7 days? | unsupported_cause | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| explain-08 | Why is crypto down today? | wrong_tool, wrong_status | clarify |  |
| explain-10 | Why did LINK rise this week? | missing_key_number | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| explain-11 | What's behind AVAX's price action today? | missing_key_number, unsupported_cause | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| explain-12 | Why is Ethereum underperforming bitcoin this week? | missing_no_clear_driver | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| explain-14 | why is btc red | missing_key_number | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| explain-15 | What caused the move in LTC today? | missing_key_number | ok | get_ticker, compute_metrics, attribute_move, get_news_sentiment |
| concept-08 | What is a stablecoin? | missing_citation | ok | search_docs |
