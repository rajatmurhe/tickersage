"""The LangGraph flow.

input_guard -> router -> [cache_lookup] -> planner -> executor -> synthesizer -> verifier -> output_guard
     |            |                           |                        ^           |
     v            v                           v                        +-- retry --+ (once)
   refuse      redirect                    clarify                                 |
                                                                                   v
                                                                              safe_answer
"""

from __future__ import annotations

import asyncio
import operator
import time
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .guardrails import FOLLOW_UPS, REFUSAL_MESSAGES, check_input, check_output, data_footer, log_refusal
from .llm import LLMClient, LLMResponse, LLMUnavailable
from .planner import Planner
from .router import Router
from .schemas import (
    RouteDecision, StepTrace, ToolCall, ToolPlan, ToolResult, VerificationReport,
)
from .semantic_cache import SemanticCache
from .symbols import SymbolRegistry
from .synthesizer import normalize_citations, synthesize
from .templates import advice_snapshot_intro, build_citations, clarify_message, render
from .tools.registry import RunOptions, Toolbox
from .verifier import verify

MAX_SYNTH_ATTEMPTS = 2
SAFE_PREFIX = (
    "I couldn't write an explanation where every number checked out against the data, so here are the "
    "figures straight from the source:\n\n"
)


class AgentState(TypedDict, total=False):
    question: str
    request_id: str
    opts: RunOptions
    guard_category: str | None
    guard_reason: str | None
    route: RouteDecision | None
    plan: ToolPlan | None
    results: list[ToolResult]
    draft: str | None
    attempts: int
    verification: VerificationReport | None
    answer: str | None
    body: str | None
    status: str
    refusal_reason: str | None
    cache_hit: bool
    removed_sentences: list[str]
    notes: list[str]
    steps: Annotated[list[StepTrace], operator.add]


@dataclass
class Deps:
    settings: Settings
    registry: SymbolRegistry
    toolbox: Toolbox
    router: Router
    planner: Planner
    llm: LLMClient | None
    semantic_cache: SemanticCache | None


def _step(name: str, started: float, resp: LLMResponse | None = None, **detail: Any) -> StepTrace:
    step = StepTrace(name=name, latency_ms=round((time.perf_counter() - started) * 1000, 1), detail=detail)
    if resp is not None:
        step.model = resp.model
        step.provider = resp.provider
        step.input_tokens = resp.usage.input_tokens + resp.usage.cache_read_tokens + resp.usage.cache_write_tokens
        step.output_tokens = resp.usage.output_tokens
        step.cost_usd = resp.cost_usd
        step.fell_back = resp.fell_back
        if resp.failed_attempts:
            step.detail["failed_attempts"] = resp.failed_attempts
    return step


async def _run_calls(deps: Deps, calls: list[ToolCall], opts: RunOptions, start_id: int = 1) -> list[ToolResult]:
    return list(
        await asyncio.gather(*(deps.toolbox.call(c, start_id + i, opts) for i, c in enumerate(calls)))
    )


def build_graph(deps: Deps):
    s = deps.settings

    # ------------------------------------------------------------- nodes

    async def input_guard(state: AgentState) -> dict:
        t = time.perf_counter()
        g = check_input(state["question"])
        return {
            "guard_category": g.category,
            "guard_reason": g.reason,
            "status": "ok",
            "attempts": 0,
            "results": [],
            "notes": [],
            "steps": [_step("input_guard", t, allowed=g.allowed, category=g.category, reason=g.reason)],
        }

    async def refuse(state: AgentState) -> dict:
        t = time.perf_counter()
        category = state.get("guard_category") or "advice"
        route = state.get("route")
        symbols = route.symbols if route else deps.registry.extract(state["question"])
        message = REFUSAL_MESSAGES[category]  # type: ignore[index]
        results: list[ToolResult] = []
        if category in ("advice", "trade") and symbols:
            calls = [ToolCall(tool="get_ticker", args={"symbol": sym}) for sym in symbols[:2]]
            results = await _run_calls(deps, calls, state.get("opts") or RunOptions())
            ok = [r for r in results if r.ok]
            if ok:
                message += "\n\n" + advice_snapshot_intro([r.data["base_asset"] for r in ok]) + "\n\n" + render(None, ok)
            else:
                message += " " + FOLLOW_UPS[category]  # type: ignore[index]
        else:
            message += " " + FOLLOW_UPS[category]  # type: ignore[index]
        reason = f"{category}: {state.get('guard_reason') or 'classified by router'}"
        log_refusal(state.get("request_id", ""), category, state.get("guard_reason"))
        return {
            "answer": message,
            "status": "refused",
            "refusal_reason": reason,
            "results": results,
            "steps": [_step("refuse", t, category=category)],
        }

    async def route_node(state: AgentState) -> dict:
        t = time.perf_counter()
        decision, resp, fallback = await deps.router.route(state["question"])
        update: dict[str, Any] = {"route": decision}
        detail = {"intent": decision.intent, "symbols": decision.symbols, "window": decision.window,
                  "source": decision.source}
        if fallback:
            detail["fallback"] = fallback
        if decision.intent in ("advice", "trade"):
            update["guard_category"] = decision.intent
            update["guard_reason"] = f"router classified the question as {decision.intent}"
        update["steps"] = [_step("router", t, resp, **detail)]
        return update

    async def redirect(state: AgentState) -> dict:
        t = time.perf_counter()
        msg = REFUSAL_MESSAGES["off_topic"] + " " + FOLLOW_UPS["off_topic"]
        return {"answer": msg, "status": "out_of_scope", "steps": [_step("redirect", t)]}

    def _cache_allowed(state: AgentState) -> bool:
        # Runs with injected faults or fixture overrides must exercise the tools, not a cached answer.
        opts = state.get("opts")
        return deps.semantic_cache is not None and not (opts and (opts.faults or opts.overrides))

    async def cache_lookup(state: AgentState) -> dict:
        t = time.perf_counter()
        hit = await deps.semantic_cache.lookup(state["question"]) if _cache_allowed(state) else None
        if not hit:
            return {"cache_hit": False, "steps": [_step("semantic_cache", t, hit=False)]}
        payload, score, matched = hit
        return {
            "cache_hit": True,
            "draft": payload["body"],
            "results": [ToolResult.model_validate(r) for r in payload["results"]],
            "steps": [_step("semantic_cache", t, hit=True, similarity=score, matched_question=matched)],
        }

    async def planner_node(state: AgentState) -> dict:
        t = time.perf_counter()
        route = state["route"]
        assert route is not None
        plan, resp, fallback = await deps.planner.plan(route, state["question"])
        update: dict[str, Any] = {"plan": plan}
        detail: dict[str, Any] = {"calls": [c.model_dump() for c in plan.calls], "mode": deps.planner.mode}
        if fallback:
            detail["fallback"] = fallback
        if not plan.calls:
            update["answer"] = clarify_message(route)
            update["status"] = "clarify"
        update["steps"] = [_step("planner", t, resp, **detail)]
        return update

    async def executor(state: AgentState) -> dict:
        t = time.perf_counter()
        plan = state["plan"]
        assert plan is not None
        results = await _run_calls(deps, plan.calls, state.get("opts") or RunOptions())
        summary = [{"tool": r.tool, "ok": r.ok, "cached": r.cached, "ms": r.latency_ms,
                    "error": r.error.code if r.error else None} for r in results]
        return {"results": results, "steps": [_step("executor", t, tools=summary)]}

    async def synthesizer(state: AgentState) -> dict:
        t = time.perf_counter()
        results = state.get("results", [])
        route = state["route"]
        assert route is not None
        attempts = state.get("attempts", 0) + 1
        if not any(r.ok for r in results):
            return {"draft": None, "attempts": attempts, "steps": [_step("synthesizer", t, skipped="all tools failed")]}
        if deps.llm and deps.llm.available:
            prior = state.get("verification")
            unsupported = prior.unsupported if prior and attempts > 1 else None
            try:
                resp = await synthesize(deps.llm, state["question"], route, results, unsupported)
            except LLMUnavailable as exc:
                notes = state.get("notes", []) + [f"llm unavailable, template answer used: {exc}"]
                return {"draft": render(route, results), "attempts": attempts, "status": "safe_fallback",
                        "notes": notes, "steps": [_step("synthesizer", t, mode="template", error=str(exc))]}
            return {"draft": normalize_citations(resp.text.strip()), "attempts": attempts,
                    "steps": [_step("synthesizer", t, resp, attempt=attempts, retry_for=unsupported)]}
        return {"draft": render(route, results), "attempts": attempts,
                "steps": [_step("synthesizer", t, mode="template")]}

    async def verifier(state: AgentState) -> dict:
        t = time.perf_counter()
        draft = state.get("draft")
        if draft is None:
            return {"steps": [_step("verifier", t, skipped=True)]}
        report = verify(draft, state.get("results", []), state["question"])
        report.attempts = state.get("attempts", 0)
        return {
            "verification": report,
            "steps": [_step("verifier", t, checked=report.checked, matched=report.matched,
                            unsupported=report.unsupported, enforced=s.verifier_enabled)],
        }

    async def safe_answer(state: AgentState) -> dict:
        t = time.perf_counter()
        results = state.get("results", [])
        route = state.get("route")
        verification = state.get("verification")
        failed_check = verification is not None and bool(verification.unsupported) and state.get("draft")
        body = (SAFE_PREFIX if failed_check else "") + render(route, results)
        return {"draft": body, "status": "safe_fallback",
                "steps": [_step("safe_answer", t, reason="verifier" if failed_check else "no usable data")]}

    async def output_guard(state: AgentState) -> dict:
        t = time.perf_counter()
        status = state.get("status", "ok")
        results = state.get("results", [])
        route = state.get("route")
        body = state.get("answer") or state.get("draft") or ""
        removed: list[str] = []
        if status not in ("refused", "out_of_scope", "clarify"):
            check = check_output(body)
            removed = check.removed
            if removed:
                body = check.text if len(check.text) > 40 else render(route, results)
        update: dict[str, Any] = {"removed_sentences": removed}
        if results and status not in ("out_of_scope", "clarify"):
            update["verification"] = verify(body, results, state["question"]).model_copy(
                update={"attempts": state.get("attempts", 0)}
            )
        market = [r for r in results if r.ok and r.as_of and r.tool != "search_docs"]
        as_of = min((r.as_of for r in market), default=None)  # type: ignore[type-var]
        footer = data_footer(as_of, [r.source for r in market if r.source])
        update["body"] = body
        update["answer"] = f"{body}\n\n{footer}"
        if (_cache_allowed(state) and route and route.intent == "concept" and status == "ok"
                and not state.get("cache_hit") and any(r.ok for r in results)):
            await deps.semantic_cache.store(  # type: ignore[union-attr]
                state["question"], {"body": body, "results": [r.model_dump(mode="json") for r in results]}
            )
        update["steps"] = [_step("output_guard", t, removed=removed)]
        return update

    # ------------------------------------------------------------- edges

    def after_guard(state: AgentState) -> str:
        return "refuse" if state.get("guard_category") else "router"

    def after_router(state: AgentState) -> str:
        route = state["route"]
        assert route is not None
        if route.intent in ("advice", "trade"):
            return "refuse"
        if route.intent == "out_of_scope":
            return "redirect"
        if route.intent == "concept" and deps.semantic_cache is not None:
            return "cache_lookup"
        return "planner"

    def after_cache(state: AgentState) -> str:
        return "output_guard" if state.get("cache_hit") else "planner"

    def after_planner(state: AgentState) -> str:
        return "output_guard" if state.get("status") == "clarify" else "executor"

    def after_verifier(state: AgentState) -> str:
        if state.get("draft") is None:
            return "safe_answer"
        report = state.get("verification")
        if s.verifier_enabled and report and report.unsupported:
            return "synthesizer" if state.get("attempts", 0) < MAX_SYNTH_ATTEMPTS else "safe_answer"
        return "output_guard"

    g = StateGraph(AgentState)
    g.add_node("input_guard", input_guard)
    g.add_node("refuse", refuse)
    g.add_node("router", route_node)
    g.add_node("redirect", redirect)
    g.add_node("cache_lookup", cache_lookup)
    g.add_node("planner", planner_node)
    g.add_node("executor", executor)
    g.add_node("synthesizer", synthesizer)
    g.add_node("verifier", verifier)
    g.add_node("safe_answer", safe_answer)
    g.add_node("output_guard", output_guard)

    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", after_guard, ["refuse", "router"])
    g.add_conditional_edges("router", after_router, ["refuse", "redirect", "cache_lookup", "planner"])
    g.add_conditional_edges("cache_lookup", after_cache, ["output_guard", "planner"])
    g.add_conditional_edges("planner", after_planner, ["output_guard", "executor"])
    g.add_edge("executor", "synthesizer")
    g.add_edge("synthesizer", "verifier")
    g.add_conditional_edges("verifier", after_verifier, ["synthesizer", "safe_answer", "output_guard"])
    g.add_edge("safe_answer", "output_guard")
    g.add_edge("refuse", "output_guard")
    g.add_edge("redirect", "output_guard")
    g.add_edge("output_guard", END)
    return g.compile()


__all__ = ["AgentState", "Deps", "build_graph", "build_citations"]
