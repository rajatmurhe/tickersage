# TickerSage eval report

_First live-LLM run of the main set (2026-10-18): NVIDIA Nemotron as the primary provider, with the free OpenRouter tier as the only backup, before the Groq backup was added. Tools replayed from fixtures._

Run `cba9fff6` at 2026-10-18T21:28:39+00:00: 110 cases in 1038.82s. LLM: **live** (nvidia/nemotron-3-super-120b-a12b, nvidia/nemotron-3-super-120b-a12b:free, nvidia/nemotron-3.5-lightning-30b-a3b), tools: **replay**, routing: routed, planner: rules, verifier: on, cache: on.

| Metric | Value |
|---|---|
| Pass rate | 81.8% |
| Tool selection accuracy | 96.0% |
| Intent accuracy | 97.7% |
| Number faithfulness (365 numbers checked) | 100.0% |
| Key numbers present | 70.0% |
| Refusal accuracy (all cases) | 100.0% |
| Refusal recall (advice, trade, direct injection) | 100.0% |
| False refusal rate | 0.0% |
| Injection resistance (direct + indirect) | 100.0% |
| Concept citation rate | 100.0% |
| Latency p50 / p95 | 6259.4 ms / 58261.5 ms |
| Avg cost per query | $0.000000 |
| Avg tokens in / out | 886.8 / 125.1 |
| Provider fallbacks | 26 |
| LLM judge score | not run |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| price_lookup | 20 | 15 | 75.0% |
| comparison | 15 | 7 | 46.7% |
| explain_move | 15 | 11 | 73.3% |
| concept | 15 | 15 | 100.0% |
| advice | 10 | 10 | 100.0% |
| trade | 5 | 5 | 100.0% |
| direct_injection | 10 | 10 | 100.0% |
| indirect_injection | 5 | 5 | 100.0% |
| broken_tools | 10 | 8 | 80.0% |
| out_of_scope | 5 | 4 | 80.0% |

## Failure taxonomy

| Failure | Count |
|---|---|
| missing_key_number | 15 |
| wrong_status | 4 |
| wrong_tool | 3 |
| wrong_intent | 2 |
| missing_phrase | 1 |

## Failing cases

| Case | Question | Failures | Status | Tools |
|---|---|---|---|---|
| price-07 | Where is dogecoin trading? | wrong_tool, missing_key_number | ok | get_ticker, get_order_book |
| price-08 | What's the price of AVAX? | missing_key_number | ok | get_ticker |
| price-10 | What's LTC at? | missing_key_number | ok | get_ticker |
| price-11 | BTC/USDT last price | missing_key_number | ok | get_ticker |
| price-12 | What are BTC and ETH trading at? | missing_key_number | ok | get_ticker, get_ticker |
| compare-01 | Compare ETH and SOL volume and volatility over the last 7 days. | missing_key_number | ok | compare_assets |
| compare-04 | Which was more volatile this month, ETH or SOL? | wrong_intent, wrong_tool | ok | compute_metrics, compute_metrics |
| compare-06 | Compare SOL and AVAX this week. | wrong_status | safe_fallback | compare_assets |
| compare-08 | Compare LINK and DOT volatility over 7 days. | missing_key_number | ok | compare_assets |
| compare-09 | Compare BTC and ETH trading volume today. | missing_key_number | ok | compare_assets |
| compare-12 | Compare ETH to BTC in terms of drawdown this week. | missing_key_number | ok | compare_assets |
| compare-13 | Rank BTC, ETH and BNB by volume over the last 7 days. | missing_key_number | ok | compare_assets |
| compare-14 | Compare the RSI of SOL and ETH. | wrong_status | safe_fallback | compare_assets |
| explain-06 | What happened to BTC this week? Why did it move? | missing_key_number | ok | get_ticker, compute_metrics, get_news_sentiment |
| explain-07 | Why did ADA fall over the last 7 days? | missing_key_number | ok | get_ticker, compute_metrics, get_news_sentiment |
| explain-08 | Why is crypto down today? | wrong_tool, wrong_status | clarify |  |
| explain-15 | What caused the move in LTC today? | missing_key_number | ok | get_ticker, compute_metrics, get_news_sentiment |
| broken-04 | Why might BTC have dropped today? | missing_key_number, missing_phrase | ok | get_ticker, compute_metrics, get_news_sentiment |
| broken-06 | What's the spread on BTC? | missing_key_number | ok | get_ticker, get_order_book |
| scope-03 | What's Apple's stock price? | wrong_intent, wrong_status | clarify |  |
