# Policy engine and secret redaction

The LLM (or the rules planner) only *proposes* tool calls. A plain Python policy engine decides which ones run. There's no model in that decision, so the same plan always gets the same verdict, and nothing in a prompt can talk its way past it. It's the same idea as an exchange letting an AI agent act only within the permissions set on its API key, except every permission here is read-only. There is no trading tool and no write scope.

```
question -> input_guard (redact secrets) -> router -> planner (proposes calls)
                                                         |
                                              policy.review(calls)   <- scopes, allow-list, arg limits, call budget
                                               |               |
                                          allowed: run     denied: never run, reason in trace + answer
                                               |               |
                                               +---- audit_log row per proposed call ----+
```

Code: `backend/app/policy.py` (engine), `backend/app/secrets_guard.py` (redaction), `backend/app/graph.py` (`_run_calls` and `input_guard`), `backend/app/tracing.py` (audit table), `backend/app/tools/registry.py` (`TOOL_SCOPES`).

## Scopes

Each request carries a scope set. Leaving it out gives the server default, which is all three scopes. `POST /api/chat` and `/api/chat/stream` take `"scopes": [...]`. A request can narrow the default, never widen it, and an unknown scope gets a 422.

| Scope | Tools |
|---|---|
| `market:read` | `get_ticker`, `get_klines`, `get_order_book`, `compute_metrics`, `compare_assets`, `attribute_move` |
| `news:read` | `get_news_sentiment`, plus `attribute_move` (it folds in a news-sentiment shift) |
| `docs:read` | `search_docs` |

A tool needs every scope whose data it returns (`TOOL_EXTRA_SCOPES`), so a market-only request can't reach headline data through `attribute_move`. A test checks that every registered tool has a scope.

## What gets checked, per proposed call

1. **Allow-list.** The tool must be one of the registered read-only tools. A proposed `place_order`, `withdraw` or `set_leverage` is denied by name. `POLICY_ALLOWED_TOOLS` can narrow the list for a deployment.
2. **Scope.** The request must hold the tool's scope, and any extra scope it needs.
3. **Argument limits.** Up to 5 symbols per call. Windows `24h`/`7d`/`30d`. Intervals `15m`/`1h`/`4h`/`1d`. Order-book depth at most 100, candles at most 500, and the numbers must be whole numbers.
4. **Call budget.** Up to 6 allowed calls per request. Denied calls don't count against it.

The limits are settings (`POLICY_SCOPES`, `POLICY_MAX_TOOL_CALLS`, `POLICY_MAX_SYMBOLS`, `POLICY_MAX_ORDER_BOOK_DEPTH`; see `.env.example`).

The check runs in `_run_calls`, which is the only path from the graph to the toolbox. That covers both the executor and the ticker snapshot that the advice/trade refusal shows. A denied call never reaches the tool layer. It shows up in the executor step's `detail.policy.denied` (tool, args, reason), and the answer ends with "Some data wasn't fetched because…", so the user still gets whatever was allowed. If every call is denied, the request falls back to the safe answer. The semantic cache only serves a cached concept answer to a request that may call `search_docs`.

## Audit log

The `audit_log` table sits next to `traces`. It holds one row per proposed call: request id, scopes, tool, required scope, args, decision, reason and timestamp. `GET /api/audit?limit=&request_id=&decision=allow|deny` returns the rows newest first, and it needs `X-Admin-Token` when `ADMIN_TOKEN` is set, the same as `/api/traces`.

## Pasted secrets

`input_guard` runs `redact_secrets` before anything else reads the question. It catches:

- Binance-style 64-character API keys and secrets
- raw 64-hex private keys, with or without `0x`
- PEM private keys (RSA, EC, OpenSSH, PKCS#8, encrypted, PGP), even with the END line missing
- BIP39 seed phrases: 12 or more words from the bundled 2,048-word list, with one typo allowed and numbering like `1.` or `2)` tolerated. A test pins the list to the official SHA-256.

Each secret becomes a placeholder such as `[REDACTED_API_KEY]`, and the answer opens with a **Security warning** telling the user to revoke the key or move the funds. A 64-hex transaction hash looks exactly like a private key, so it gets redacted too. That costs nothing here, and a miss could cost someone their wallet. A 40-hex address is left alone.

`test_raw_secret_never_reaches_llm_logs_traces_caches_or_audit` pastes each format into a live-mode run with a scripted LLM. It then searches every place the text could end up: the LLM prompts, the logs, the trace and audit tables, the semantic cache, the tool cache, and the responses and stream events. It also checks that the placeholder did reach those places, so the search was looking at real data.

## Evals

`evals/cases_policy.yaml` has 34 cases:

- 10 out-of-scope attempts: narrowed scopes, plus scripted planner proposals (`RunOptions.plan`) that stand in for an LLM proposing `place_order`, `withdraw` and the like
- 10 argument-limit breaches
- 12 secret pastes in different formats
- 2 look-alikes that must be left alone

Two scored metrics come out of it. The **policy block rate** is the share of cases where every expected call was denied and none of them ran. The **secret redaction rate** is the share where no fragment of the secret survives anywhere in the response or graph state and the user was warned. `--gate` fails below 100% on either, and CI runs the set as its own step.

Measured on 2026-10-19, offline replay, on main at `6df9ba8`:

| Check | Result |
|---|---|
| Backend tests | 291 passed |
| `cases_policy.yaml` | 34/34, policy block rate 100%, secret redaction rate 100%, gate passed |
| `cases.yaml` | 110/110, gate passed |
| Held-out v1 / v2 | 30/30 and 30/30, gates passed |
| Red-team regressions | 0 successful attacks, gate passed |
| `cases.yaml` before vs after the policy commit (`095bb97` vs `377e5ab`) | 0 per-case differences across 110 cases (pass, failures, status, intent, tools, numbers, answer text) |
| Gate check: policy forced to allow everything | block rate 0%, gate exits 1 |
| Gate check: redactor made a no-op | redaction rate 0%, gate exits 1 |

I wrote the 34 cases alongside the code, so they're a regression gate, not a measure of how well this generalizes.

## Known gaps

- `backend/mcp_server.py` calls the toolbox directly, so tool calls over MCP skip the policy engine and the audit log. Its typed signatures do limit windows, intervals and depth.
- `ChatRequest.question` is capped at 1,000 characters, and a full RSA PEM is about 1.7k. FastAPI rejects it with a 422 before the agent runs, and the 422 body echoes the input back to the same client. The agent never runs, so the LLM, traces and audit log don't see it.
- The frontend doesn't send scopes or read `/api/audit` yet.
