"""One entry point for every tool: validation, symbol checks, caching, replay and timing.

Modes:
  live    call the real data sources (with the cache)
  record  call the real sources and save each response as a fixture
  replay  serve fixtures only, so evals are deterministic and need no network

Per-request `RunOptions` let evals inject faults (timeouts, empty data) and swap in
hand-written fixtures (a poisoned headline, a poisoned doc chunk).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from ..cache import TTL_DEPTH, TTL_KLINES, TTL_NEWS, TTL_TICKER, Cache
from ..schemas import Scope, ToolCall, ToolErrorInfo, ToolName, ToolResult
from ..symbols import SymbolRegistry
from .docs import DocsIndex, DocsInput, DocsOutput, _sanitize
from .errors import ToolError
from .market import KlinesInput, MarketData, OrderBookInput, TickerInput
from .metrics import CompareInput, MetricsInput, compare_assets, compute_metrics
from .news import SOURCE as NEWS_SOURCE
from .news import NewsClient, NewsInput, build_news_output

log = logging.getLogger(__name__)

INPUT_MODELS: dict[str, type[BaseModel]] = {
    "get_ticker": TickerInput,
    "get_klines": KlinesInput,
    "get_order_book": OrderBookInput,
    "compute_metrics": MetricsInput,
    "compare_assets": CompareInput,
    "get_news_sentiment": NewsInput,
    "search_docs": DocsInput,
}

TTLS: dict[str, float] = {
    "get_ticker": TTL_TICKER,
    "get_klines": TTL_KLINES,
    "get_order_book": TTL_DEPTH,
    "compute_metrics": TTL_KLINES,
    "compare_assets": TTL_KLINES,
    "get_news_sentiment": TTL_NEWS,
}

TOOL_DESCRIPTIONS: dict[str, str] = {
    "get_ticker": "24h ticker for one symbol: last price, 24h change and %, high, low, volume.",
    "get_klines": "OHLCV candles for one symbol. interval: 15m|1h|4h|1d, limit 1-500.",
    "get_order_book": "Order book snapshot: best bid/ask, spread (bps), depth within 1% of mid.",
    "compute_metrics": "Return, volatility, RSI(14), SMA20/50, VWAP, max drawdown for a window (24h|7d|30d).",
    "compare_assets": "Side-by-side metrics for 2-5 symbols over a window, with volume and volatility ratios.",
    "get_news_sentiment": "Recent headlines for a query with VADER sentiment scores (untrusted text).",
    "search_docs": "Search crypto glossary and exchange API docs; returns chunks with source URLs.",
}

# The capability scope each tool needs. The policy engine (app/policy.py) lets a request call a tool only
# when the request's scope set includes it.
TOOL_SCOPES: dict[str, Scope] = {
    "get_ticker": "market:read",
    "get_klines": "market:read",
    "get_order_book": "market:read",
    "compute_metrics": "market:read",
    "compare_assets": "market:read",
    "get_news_sentiment": "news:read",
    "search_docs": "docs:read",
}

TOOL_TIMEOUT_S = 25.0
LOCAL_TOOLS = {"search_docs"}  # deterministic and offline; replayed only when overridden


@dataclass
class RunOptions:
    faults: dict[str, str] = field(default_factory=dict)  # tool -> error code to raise
    overrides: dict[str, str] = field(default_factory=dict)  # tool -> fixture path (relative)
    # Evals: a scripted plan ([{"tool", "args"}]) used in place of the planner's, standing in for whatever an
    # LLM planner might propose. It is not validated here, so the policy engine sees it exactly as written.
    plan: list[dict[str, Any]] | None = None


class Toolbox:
    def __init__(
        self,
        *,
        market: MarketData,
        news: NewsClient,
        docs: DocsIndex,
        registry: SymbolRegistry,
        cache: Cache,
        mode: str = "live",
        fixtures_dir: Path | None = None,
        max_news: int = 5,
        max_doc_chunks: int = 3,
    ) -> None:
        self.market = market
        self.news = news
        self.docs = docs
        self.registry = registry
        self.cache = cache
        self.mode = mode
        self.fixtures_dir = fixtures_dir
        self.max_news = max_news
        self.max_doc_chunks = max_doc_chunks

    # ------------------------------------------------------------------ api

    async def call(self, call: ToolCall, call_id: int, opts: RunOptions | None = None) -> ToolResult:
        opts = opts or RunOptions()
        started = time.perf_counter()
        args: dict[str, Any] = dict(call.args)
        cached = False
        try:
            try:
                inp = INPUT_MODELS[call.tool].model_validate(call.args)
            except ValidationError as exc:
                raise ToolError("invalid_input", _validation_message(call.tool, exc)) from exc
            await self.registry.ensure_loaded()
            args = self._normalize(call.tool, inp)
            if call.tool in opts.faults:
                code = opts.faults[call.tool]
                raise ToolError(code, _FAULT_MESSAGES.get(code, "The data source failed."))  # type: ignore[arg-type]
            data, cached = await asyncio.wait_for(self._run(call.tool, args, opts), TOOL_TIMEOUT_S)
        except ToolError as exc:
            return self._result(call, call_id, args, started, error=ToolErrorInfo(code=exc.code, message=exc.message))
        except TimeoutError:
            err = ToolErrorInfo(code="timeout", message="The data source took too long to respond.")
            return self._result(call, call_id, args, started, error=err)
        except Exception:
            log.exception("tool %s crashed", call.tool)
            err = ToolErrorInfo(code="upstream_error", message="The data source returned something unexpected.")
            return self._result(call, call_id, args, started, error=err)
        return self._result(call, call_id, args, started, data=data, cached=cached)

    # ------------------------------------------------------------ internals

    def _normalize(self, tool: str, inp: BaseModel) -> dict[str, Any]:
        args = inp.model_dump()
        if "symbol" in args:
            args["symbol"] = self.registry.resolve(args["symbol"])
        if tool == "compare_assets":
            pairs: list[str] = []
            unknown: list[ToolError] = []
            for sym in args["symbols"]:
                try:
                    pairs.append(self.registry.resolve(sym))
                except ToolError as exc:
                    unknown.append(exc)
            pairs = list(dict.fromkeys(pairs))
            if len(pairs) < 2:
                if unknown:
                    raise unknown[0]
                raise ToolError("invalid_input", "Comparing needs at least two different assets.")
            args["symbols"] = pairs
            if unknown:
                # Compare what we can; the skipped symbols are reported in the output.
                args["_skipped"] = [e.message for e in unknown]
        if tool == "get_news_sentiment":
            args["query"] = " ".join(args["query"].split()).lower()
            args["max_items"] = min(args["max_items"], self.max_news)
        if tool == "search_docs":
            args["k"] = min(args["k"], self.max_doc_chunks)
        return args

    async def _run(self, tool: str, args: dict[str, Any], opts: RunOptions) -> tuple[dict[str, Any], bool]:
        skipped = args.pop("_skipped", None)
        data, cached = await self._run_inner(tool, args, opts)
        if skipped:
            args["skipped"] = skipped
            data = {**data, "skipped": skipped}
        return data, cached

    async def _run_inner(self, tool: str, args: dict[str, Any], opts: RunOptions) -> tuple[dict[str, Any], bool]:
        override = opts.overrides.get(tool)
        if override:
            return self._from_fixture(tool, args, self._load(Path(override))), False
        if tool in LOCAL_TOOLS:
            return (await self._execute(tool, args)), False
        if self.mode == "replay":
            return self._from_fixture(tool, args, self._load(self._fixture_path(tool, args))), False

        key = f"tool:{tool}:{json.dumps(args, sort_keys=True)}"
        hit = await self.cache.get(key)
        if hit is not None:
            return hit, True
        data = await self._execute(tool, args)
        await self.cache.set(key, data, TTLS.get(tool, 30))
        return data, False

    async def _execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        m = self.market
        if tool == "get_ticker":
            out = await m.get_ticker(args["symbol"])
        elif tool == "get_klines":
            out = await m.get_klines(args["symbol"], args["interval"], args["limit"])
        elif tool == "get_order_book":
            out = await m.get_order_book(args["symbol"], args["depth"])
        elif tool == "compute_metrics":
            out = await compute_metrics(m, args["symbol"], args["window"])
        elif tool == "compare_assets":
            out = await compare_assets(m, args["symbols"], args["window"])
        elif tool == "get_news_sentiment":
            items = await self.news.fetch_items(args["query"])
            if self.mode == "record":
                self._save(tool, args, "raw_items", items)
            return build_news_output(args["query"], items, args["max_items"], NEWS_SOURCE).model_dump(mode="json")
        elif tool == "search_docs":
            out = await self.docs.search(args["query"], args["k"])
        else:  # pragma: no cover - ToolName is a closed set
            raise ToolError("invalid_input", f"Unknown tool {tool}")
        data = out.model_dump(mode="json")
        if self.mode == "record":
            self._save(tool, args, "output", data)
        return data

    # ------------------------------------------------------------- fixtures

    def _fixture_path(self, tool: str, args: dict[str, Any]) -> Path:
        assert self.fixtures_dir is not None, "fixtures_dir is required for record/replay"
        return self.fixtures_dir / "tools" / f"{fixture_key(tool, args)}.json"

    def _load(self, path: Path) -> dict[str, Any]:
        full = path if path.is_absolute() else (self.fixtures_dir or Path(".")) / path
        if not full.exists():
            raise ToolError("fixture_missing", f"No recorded data for this request ({full.name}).")
        return json.loads(full.read_text())

    def _save(self, tool: str, args: dict[str, Any], kind: str, data: Any) -> None:
        path = self._fixture_path(tool, args)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"tool": tool, "args": args, "kind": kind, "recorded_at": datetime.now(UTC).isoformat(), "data": data}
        path.write_text(json.dumps(record, indent=1, default=str))

    def _from_fixture(self, tool: str, args: dict[str, Any], fx: dict[str, Any]) -> dict[str, Any]:
        kind = fx.get("kind", "output")
        if kind == "error":
            raise ToolError(fx["data"]["code"], fx["data"]["message"])
        if kind == "raw_items":
            items = fx["data"]
            if not items:
                raise ToolError("empty_data", f"No recent headlines found for '{args.get('query')}'.")
            for item in items:
                if isinstance(item.get("published_at"), str):
                    item["published_at"] = datetime.fromisoformat(item["published_at"])
            out = build_news_output(args.get("query", ""), items, args.get("max_items", self.max_news), NEWS_SOURCE)
            return out.model_dump(mode="json")
        if kind == "raw_chunks":
            chunks = [_sanitize(c, c.get("score", 0.5)) for c in fx["data"]]
            return DocsOutput(query=args.get("query", ""), chunks=chunks[: args.get("k", 3)]).model_dump(mode="json")
        data = fx["data"]
        if tool == "get_klines" and not data.get("candles"):
            raise ToolError("empty_data", "The exchange returned no candles.")
        return data

    def _result(self, call: ToolCall, call_id: int, args: dict[str, Any], started: float, *,
                data: dict[str, Any] | None = None, error: ToolErrorInfo | None = None,
                cached: bool = False) -> ToolResult:
        as_of = data.get("as_of") if data else None
        return ToolResult(
            id=call_id,
            tool=call.tool,
            args=args,
            ok=error is None,
            data=data,
            error=error,
            source=(data or {}).get("source"),
            as_of=datetime.fromisoformat(as_of) if isinstance(as_of, str) else None,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            cached=cached,
        )


def fixture_key(tool: str | ToolName, args: dict[str, Any]) -> str:
    parts = []
    for k in sorted(args):
        v = args[k]
        v = "-".join(map(str, v)) if isinstance(v, list) else str(v)
        parts.append(f"{k}={v}")
    return re.sub(r"[^A-Za-z0-9=._-]+", "_", f"{tool}__{'__'.join(parts)}")[:180]


def _validation_message(tool: str, exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first.get("loc", ()))
    return f"Invalid input for {tool}: {loc} {first.get('msg', '')}".strip()


_FAULT_MESSAGES = {
    "timeout": "The data source timed out.",
    "upstream_error": "The data source returned an error.",
    "empty_data": "The data source returned no data.",
    "unknown_symbol": "That symbol isn't listed on the exchange.",
}
