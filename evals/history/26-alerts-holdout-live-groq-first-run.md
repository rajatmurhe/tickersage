# TickerSage smart alerts eval report

_Smart alerts held-out set (`cases_alerts_holdout.yaml`, 30 cases), first run of the LLM compiler against a live model, 2026-10-20, at commit 66492a0: Groq's free tier, openai/gpt-oss-20b (the fast tier), strict JSON schema, low reasoning effort, no other provider configured. Nothing in the compiler, prompt or cases was tuned on this set before the run; the only earlier live calls were two probes on one dev case (the second after moving request_type to the end of the schema, which the model had left out). 6 refused inputs never reach the model. Latency includes the SDK's waits on Groq 429s (LLM_MAX_RETRIES=4). Failure types: logic read as "any" for conditions joined by "and" (1), a condition repeated (1), the asset left null although the text names DOGE (1), a news alert classified as an advice request (1, the false refusal), "moons" given no condition (1); 2 replies failed Groq's schema check (an invalid direction value) and fell back to the rules parser, which then compiled them correctly._

Run `85010b4e` at 2026-10-20T07:57:15+00:00 on `evals/cases_alerts_holdout.yaml`: 30 cases in 235.63s. Compiler: **llm**, data: **replay** (recorded candles, tickers and headlines).

| Metric | Value |
|---|---|
| Pass rate | 83.3% |
| Spec accuracy (alerts compiled to the expected spec) | 76.5% |
| Clarifying questions (exactly the expected fields) | 85.7% |
| Rejection recall (trade, advice, injection) | 100.0% |
| False rejections | 1 |
| Fire accuracy on replayed data | n/a |
| Scheduler decisions (fire, re-arm, cooldown) | n/a |
| Briefs written / numbers checked | 0 / 0 |
| Brief number faithfulness | n/a |
| Briefs that fell back to the template | 0 |
| Advice sentences in briefs | 0 |
| Compile latency p50 | 9563.65 ms |
| Alerts sent to the model | 24 |
| Compiled from the model's reply | 22 |
| Refused by the model (trade or advice) | 1 |
| Fell back to the rules parser | 2 (llm unavailable (2)) |
| Served by | groq/openai/gpt-oss-20b (22) |
| Answered by a backup provider | 0 |
| Numbers dropped by grounding (not in the text) | 0 in 0 case(s) |
| Model cost | $0.0000 |
| Model latency p50 / p95 | 10668.2 / 15382.1 ms |
| Model tokens in / out | 23013 / 3525 |

## By category

| Category | Cases | Passed | Pass rate |
|---|---|---|---|
| compile | 17 | 13 | 76.5% |
| clarify | 7 | 6 | 85.7% |
| reject | 6 | 6 | 100.0% |

## Rules fallbacks and grounding drops

| Case | Passed | What happened |
|---|---|---|
| hc-23 | yes | fell back to rules: llm unavailable: groq/openai/gpt-oss-20b: HTTP 400: Error code: 400 - {'error': {'message': "Generated JSON does not match the expected schema. Please adjust your prompt. See 'failed_generation' for more details. Error: jsonschema: '/conditions/0/direction' does not validate with /properties/conditions/items/properties/direction/anyOf/0/enum: value must be one of 'up', 'down', 'either'", 'type': 'invalid_request_error', 'code': 'json_validate_failed', 'failed_generation': '{"symbol":null,"other_symbols":[],"conditions":[{"kind":"rsi","op":"above","value":80,"period":null,"interval":"4h","fast":null,"slow":null,"direction":"null","pct":null,"window_hours":null,"price":null,"tone":null,"threshold":null}],"logic":"all","cooldown_minutes":null,"unsupported":[],"request_type":"alert"}'}} |
| hc-30 | yes | fell back to rules: llm unavailable: groq/openai/gpt-oss-20b: HTTP 400: Error code: 400 - {'error': {'message': "Generated JSON does not match the expected schema. Please adjust your prompt. See 'failed_generation' for more details. Error: jsonschema: '/conditions/0/direction' does not validate with /properties/conditions/items/properties/direction/anyOf/0/enum: value must be one of 'up', 'down', 'either'", 'type': 'invalid_request_error', 'code': 'json_validate_failed', 'failed_generation': '{"symbol":"BTC","other_symbols":[],"conditions":[{"kind":"rsi","op":"above","value":70,"period":null,"interval":"1h","fast":null,"slow":null,"direction":"both","pct":null,"window_hours":null,"price":null,"tone":null,"threshold":null},{"kind":"news_sentiment","op":null,"value":null,"period":null,"interval":null,"fast":null,"slow":null,"direction":"both","pct":null,"window_hours":null,"price":null,"tone":"positive","threshold":null}],"logic":"all","cooldown_minutes":null,"unsupported":[],"request_type":"alert"}'}} |

## Failing cases

| Case | Text | Failures |
|---|---|---|
| hc-02 | notify me if Solana is down more than 9% over 24 hours and the news is bearish | logic='any' (expected 'all') |
| hc-05 | Let me know when LTC's 4-hour RSI(21) gets above 72 | conditions: 2 (['rsi', 'rsi']), expected 1 |
| hc-09 | If DOGE moves 4 percent in either direction within 2 hours, tell me | status needs_clarification, asked about ['symbol'] |
| hc-10 | Tell me about upbeat headlines on chainlink | falsely rejected (advice) |
| hc-19 | alert me when ADA moons | asked about ['condition'], expected ['threshold', 'window'] |
