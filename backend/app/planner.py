"""Turns a route into validated tool calls.

The rules planner is the default: the intents form a closed set, and a fixed plan per
intent is cheaper and more predictable than asking a model. PLANNER_MODE=llm asks the
fast model for a plan instead; its output is validated against each tool's input
schema and replaced by the rules plan if anything is invalid.
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from .llm import LLMClient, LLMResponse, LLMUnavailable
from .prompts import PLANNER_SCHEMA, PLANNER_SYSTEM
from .schemas import RouteDecision, ToolCall, ToolPlan
from .symbols import asset_name
from .tools.registry import INPUT_MODELS

log = logging.getLogger(__name__)

NEEDS_SYMBOL = {"price", "metrics", "compare", "explain_move"}
DEFAULT_WINDOW = {"metrics": "7d", "compare": "7d", "explain_move": "24h"}


def plan_rules(route: RouteDecision, question: str) -> ToolPlan:
    syms = route.symbols
    if route.intent in NEEDS_SYMBOL and not syms:
        return ToolPlan(calls=[], note="need_symbol")
    window = route.window or DEFAULT_WINDOW.get(route.intent, "24h")
    calls: list[ToolCall] = []
    if route.intent == "price":
        calls = [ToolCall(tool="get_ticker", args={"symbol": s}) for s in syms[:3]]
        if route.wants_order_book:
            calls.append(ToolCall(tool="get_order_book", args={"symbol": syms[0], "depth": 100}))
    elif route.intent == "metrics":
        calls = [ToolCall(tool="compute_metrics", args={"symbol": s, "window": window}) for s in syms[:2]]
        if route.wants_order_book:
            calls.append(ToolCall(tool="get_order_book", args={"symbol": syms[0], "depth": 100}))
    elif route.intent == "compare":
        if len(syms) < 2:
            calls = [ToolCall(tool="compute_metrics", args={"symbol": syms[0], "window": window})]
        else:
            calls = [ToolCall(tool="compare_assets", args={"symbols": syms[:5], "window": window})]
    elif route.intent == "explain_move":
        s = syms[0]
        calls = [
            ToolCall(tool="get_ticker", args={"symbol": s}),
            ToolCall(tool="compute_metrics", args={"symbol": s, "window": window}),
            ToolCall(tool="attribute_move", args={"symbol": s, "window": window}),
            ToolCall(tool="get_news_sentiment", args={"query": asset_name(s)}),
        ]
    elif route.intent == "concept":
        calls = [ToolCall(tool="search_docs", args={"query": route.topic or question})]
    return ToolPlan(calls=calls)


def _call_from_llm(item: dict) -> ToolCall:
    tool = item["tool"]
    fields = INPUT_MODELS[tool].model_fields
    args = {k: v for k, v in item.items() if k != "tool" and v is not None and k in fields}
    INPUT_MODELS[tool].model_validate(args)  # raises on bad arguments
    return ToolCall(tool=tool, args=args)


class Planner:
    def __init__(self, llm: LLMClient | None, mode: str = "rules") -> None:
        self.llm = llm
        self.mode = mode

    async def plan(self, route: RouteDecision, question: str) -> tuple[ToolPlan, LLMResponse | None, str | None]:
        rules = plan_rules(route, question)
        if self.mode != "llm" or not self.llm or not self.llm.available or not rules.calls:
            return rules, None, None
        user = (
            f"<question>{question}</question>\n"
            f"<route>{route.model_dump_json(include={'intent', 'symbols', 'window', 'wants_order_book'})}</route>"
        )
        try:
            resp = await self.llm.generate("fast", PLANNER_SYSTEM, user, json_schema=PLANNER_SCHEMA,
                                           max_tokens=2000, effort="low")
            calls = [_call_from_llm(c) for c in json.loads(resp.text)["calls"]][:4]
            if not calls:
                return rules, resp, "empty llm plan"
            return ToolPlan(calls=calls), resp, None
        except LLMUnavailable as exc:
            return rules, None, f"llm unavailable: {exc}"
        except (ValueError, KeyError, ValidationError) as exc:
            log.warning("invalid LLM plan, using rules plan: %s", exc)
            return rules, None, f"invalid llm plan: {exc}"
