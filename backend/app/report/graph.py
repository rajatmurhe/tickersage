"""The research-report graph: a planner, one sub-agent per asset running in parallel, and a writer.

    input_guard -> planner --Send--> asset_agent (one per asset, in parallel) --> writer -> END
         |            |     \\-Send--> comparer (2+ assets, in parallel with them) -/
         v            v
       refuse      clarify / redirect

input_guard redacts pasted secrets and runs the chat's input rules (advice, trade, injection). The planner
reuses the chat router to find the assets and window (an LLM router also gets a second say on advice), then
splits the request into one task per asset. Each sub-agent proposes its read-only tool calls, the policy
engine reviews them (denied calls never run), and the sub-agent writes its section from its own data only;
the number verifier checks it, with one retry and then a template. The writer merges the sections, writes
the summary and risks (verified against every tool output), builds the charts and the comparison table, and
assembles the report.
"""

from __future__ import annotations

import asyncio
import operator
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ..graph import Deps, _step
from ..guardrails import FOLLOW_UPS, REFUSAL_MESSAGES, check_input, log_refusal
from ..tools.metrics import WINDOW_SPEC
from ..policy import Policy, denial_note
from ..router import _FOREX, _NON_CRYPTO
from ..schemas import RouteDecision, StepTrace, ToolCall, ToolResult, VerificationReport
from ..secrets_guard import redact_secrets
from ..symbols import asset_name
from ..templates import TOOL_LABELS, build_citations, compare_block
from ..tools.errors import ToolError
from ..tools.registry import RunOptions
from ..verifier import verify
from .charts import build_charts
from .models import AssetSection, SectionCheck
from .render import body_markdown, full_markdown, subtitle, title_for
from .writing import (
    first, guard, renumber, risks_template, section_template, split_writer, standing_caveats, summary_template,
    write_section, write_summary,
)

DEFAULT_WINDOW = "7d"
COMPARE_ID = 900  # result ids: asset i's calls are 100*(i+1)+j, the comparison is 900, so they never collide
CLARIFY = ("Which assets should the report cover? For example: \"Weekly report on BTC, ETH and SOL\" or "
           "\"Monthly report on Solana\".")
REPORT_FOLLOW_UP = ("I can write a neutral research report instead, with prices, returns, volatility, what moved "
                    "each asset and recent news tone: try \"Weekly report on BTC, ETH and SOL\".")


class ReportState(TypedDict, total=False):
    question: str
    request_id: str
    opts: RunOptions
    scopes: list[str]
    redactions: list[str]
    guard_category: str | None
    guard_reason: str | None
    route: RouteDecision | None
    assets: list[str]
    window: str
    status: str
    message: str | None
    planner_notes: list[str]  # assets left out, shown with the report's caveats
    notes: Annotated[list[str], operator.add]
    results: Annotated[list[ToolResult], operator.add]
    sections: Annotated[list[dict[str, Any]], operator.add]
    policy_decisions: Annotated[list[dict[str, Any]], operator.add]
    steps: Annotated[list[StepTrace], operator.add]
    report: dict[str, Any]


def asset_calls(asset: str, window: str) -> list[ToolCall]:
    """What a sub-agent gathers for one asset: the chat's read-only tools plus candles for the price chart."""
    interval, candles, _ = WINDOW_SPEC[window]
    return [
        ToolCall(tool="get_ticker", args={"symbol": asset}),
        ToolCall(tool="compute_metrics", args={"symbol": asset, "window": window}),
        ToolCall(tool="attribute_move", args={"symbol": asset, "window": window}),
        ToolCall(tool="get_news_sentiment", args={"query": asset_name(asset)}),
        ToolCall(tool="get_klines", args={"symbol": asset, "interval": interval, "limit": candles}),
    ]


def build_report_graph(deps: Deps):
    def policy(state: dict[str, Any]) -> Policy:
        return deps.policy.for_request(state.get("scopes"))

    # ------------------------------------------------------------------ nodes

    async def input_guard(state: ReportState) -> dict:
        t = time.perf_counter()
        redaction = redact_secrets(state["question"])
        g = check_input(redaction.text)
        detail: dict[str, Any] = {"allowed": g.allowed, "category": g.category, "reason": g.reason}
        update: dict[str, Any] = {}
        if redaction.found:
            update = {"question": redaction.text, "redactions": redaction.kinds}
            detail["secrets_redacted"] = redaction.kinds
        return {**update, "guard_category": g.category, "guard_reason": g.reason, "status": "ok",
                "steps": [_step("input_guard", t, **detail)]}

    async def refuse(state: ReportState) -> dict:
        t = time.perf_counter()
        category = state.get("guard_category") or "advice"
        follow = REPORT_FOLLOW_UP if category in ("advice", "trade") else FOLLOW_UPS[category]  # type: ignore[index]
        log_refusal(state.get("request_id", ""), category, state.get("guard_reason"))
        return {"status": "refused", "message": REFUSAL_MESSAGES[category] + " " + follow,  # type: ignore[index]
                "notes": [f"{category}: {state.get('guard_reason') or 'classified by router'}"],
                "steps": [_step("refuse", t, category=category)]}

    async def planner(state: ReportState) -> dict:
        t = time.perf_counter()
        route, resp, fallback = await deps.router.route(state["question"])
        detail: dict[str, Any] = {"intent": route.intent, "router": route.source}
        if fallback:
            detail["fallback"] = fallback
        update: dict[str, Any] = {"route": route}
        if route.intent in ("advice", "trade"):
            update.update(guard_category=route.intent, guard_reason=f"router classified the request as {route.intent}")
            return {**update, "steps": [_step("planner", t, resp, **detail)]}
        # A concept or out-of-scope label clears the router's symbols; the request may still name assets.
        candidates = route.symbols or deps.registry.extract(state["question"])
        assets, unknown = [], []
        for sym in candidates:
            try:
                base = deps.registry.base(deps.registry.resolve(sym))
            except ToolError:
                unknown.append(sym)
                continue
            if base not in assets:
                assets.append(base)
        limit = policy(state).max_symbols
        notes = [f"{u} isn't listed on the exchange, so it's left out." for u in unknown]
        if len(assets) > limit:
            notes.append(f"A report covers at most {limit} assets; {', '.join(assets[limit:])} left out.")
            assets = assets[:limit]
        window = route.window or DEFAULT_WINDOW
        detail.update(assets=assets, window=window, unknown=unknown,
                      tasks=[{"asset": a, "tools": [c.tool for c in asset_calls(a, window)]} for a in assets])
        if not assets:
            # Only a request about something other than crypto is out of scope; "a weekly market report" just
            # needs its assets named.
            off_topic = not candidates and bool(_FOREX.search(state["question"]) or _NON_CRYPTO.search(state["question"]))
            status = "out_of_scope" if off_topic else "clarify"
            message = (REFUSAL_MESSAGES["off_topic"] + " " + REPORT_FOLLOW_UP.split(": ", 1)[0].replace(
                "instead", "on listed crypto assets") + ": try \"Weekly report on BTC, ETH and SOL\"."
                if status == "out_of_scope" else " ".join(notes + [CLARIFY]))
            update.update(status=status, message=message)
        return {**update, "assets": assets, "window": window, "planner_notes": notes,
                "steps": [_step("planner", t, resp, **detail)]}

    async def asset_agent(task: dict[str, Any]) -> dict:
        """One sub-agent: gather this asset's data through the policy engine, then write its section."""
        t = time.perf_counter()
        asset, window, index = task["asset"], task["window"], task["index"]
        calls = asset_calls(asset, window)
        decisions = policy(task).review(calls)  # each sub-agent gets the per-request tool budget
        opts = task.get("opts") or RunOptions()
        allowed = [(j, c) for j, (c, d) in enumerate(zip(calls, decisions, strict=True)) if d.allowed]
        results = list(await asyncio.gather(*(
            deps.toolbox.call(c, 100 * (index + 1) + j, opts) for j, c in allowed
        )))
        missing = [TOOL_LABELS[d.tool] for d in decisions if not d.allowed]
        _, cites, _ = build_citations(results)
        resps: list[Any] = []
        error = None
        text: str | None = None
        check = SectionCheck(mode="template")
        if deps.llm and deps.llm.available and any(r.ok for r in results):
            text, check, resps, error = await write_section(deps.llm, task["question"], asset, window, results,
                                                            cites, missing)
        template = section_template(results, cites, missing)
        if text is None:
            text = template
        text, removed = guard(text)
        if removed:
            check.removed = removed
            if len(text) < 40:
                text = template
        section = {"asset": asset, "index": index, "markdown": text, "check": check.model_dump(),
                   "cites": {n: rid for rid, n in cites.items()},
                   "tools": [{"tool": r.tool, "ok": r.ok, "error": r.error.code if r.error else None,
                              "ms": r.latency_ms} for r in results]}
        detail: dict[str, Any] = {"asset": asset, "mode": check.mode, "attempts": check.attempts,
                                  "unsupported": check.unsupported, "tools": section["tools"],
                                  "denied": [{"tool": d.tool, "reason": d.reason} for d in decisions if not d.allowed]}
        if error:
            detail["error"] = error
        steps = [_step("asset_agent", t, r, **detail, attempt=i + 1) for i, r in enumerate(resps)] or \
            [_step("asset_agent", t, **detail)]
        return {"results": results, "policy_decisions": [d.as_dict() for d in decisions], "sections": [section],
                "notes": [f"{asset}: {error}"] if error else [], "steps": steps}

    async def comparer(task: dict[str, Any]) -> dict:
        t = time.perf_counter()
        call = ToolCall(tool="compare_assets", args={"symbols": task["assets"], "window": task["window"]})
        decision = policy(task).check(call.tool, dict(call.args))
        results = [await deps.toolbox.call(call, COMPARE_ID, task.get("opts") or RunOptions())] \
            if decision.allowed else []
        detail = {"assets": task["assets"], "ok": bool(results and results[0].ok),
                  "denied": None if decision.allowed else decision.reason}
        return {"results": results, "policy_decisions": [decision.as_dict()], "steps": [_step("comparer", t, **detail)]}

    async def writer(state: ReportState) -> dict:
        t = time.perf_counter()
        assets, window, question = state["assets"], state["window"], state["question"]
        results = sorted(state.get("results", []), key=lambda r: r.id)
        citations, global_cites, _ = build_citations(results)
        by_asset = {a: [r for r in results if 100 * (i + 1) <= r.id < 100 * (i + 2)] for i, a in enumerate(assets)}
        compare = next((r for r in results if r.tool == "compare_assets" and r.ok and r.data), None)
        compare_failed = next((r for r in results if r.tool == "compare_assets" and not r.ok), None)

        sections = sorted(state.get("sections", []), key=lambda s: s["index"])
        merged = [(s["asset"], renumber(s["markdown"], {int(k): v for k, v in s["cites"].items()}, global_cites))
                  for s in sections]

        resps: list[Any] = []
        error = None
        writer_check = SectionCheck(mode="template")
        written: str | None = None
        if deps.llm and deps.llm.available and any(r.ok for r in results):
            written, writer_check, resps, error = await write_summary(deps.llm, question, assets, window, results,
                                                                     global_cites)
        if written is not None:
            summary, risks = split_writer(written)
            summary, removed_s = guard(summary)
            kept = [guard(r) for r in risks]
            risks = [text for text, _ in kept if text]
            writer_check.removed = removed_s + [x for _, rm in kept for x in rm]
            if removed_s and len(summary) < 40:
                summary = summary_template(assets, window, by_asset, compare, global_cites)
        else:
            summary = summary_template(assets, window, by_asset, compare, global_cites)
            risks = risks_template(assets, by_asset, global_cites)

        denied = [d for d in state.get("policy_decisions", []) if d["decision"] == "deny"]
        gaps = list(state.get("planner_notes") or [])
        if compare_failed and compare_failed.error:
            gaps.append(f"The side-by-side comparison couldn't be fetched: {compare_failed.error.message}")
        if any(not r.ok for r in results if r.tool != "compare_assets"):
            gaps.append("Some data couldn't be fetched; the sections above say which, and nothing is guessed.")
        has_news = any(r.ok and r.tool == "get_news_sentiment" for r in results)
        risks = risks + standing_caveats(gaps, has_news)
        comparison = compare_block(compare.data, global_cites.get(compare.id)) if compare else None  # type: ignore[arg-type]

        body = body_markdown(summary, merged, comparison, risks)
        verification = verify(body, results, question)
        market = [r for r in results if r.ok and r.as_of and r.tool != "get_news_sentiment"]
        as_of = min((r.as_of for r in market), default=None)  # type: ignore[type-var]
        title = title_for(window, assets)
        sub = subtitle(window, as_of, [r.source for r in market if r.source])
        denial = denial_note(denied) if denied else None
        charts = build_charts(assets, by_asset, global_cites)
        fell_back = writer_check.fell_back or any(s["check"]["fell_back"] for s in sections)
        report = {
            "title": title, "subtitle": sub, "summary": summary, "risks": risks, "comparison": comparison,
            "sections": [AssetSection(asset=a, name=asset_name(a), markdown=m,
                                      check=SectionCheck(**s["check"]), tools=s["tools"]).model_dump()
                         for (a, m), s in zip(merged, sections, strict=True)],
            "citations": [c.model_dump() for c in citations], "charts": charts.model_dump(),
            "markdown": full_markdown(title, sub, body, citations, denial), "body": body, "denial": denial,
            "writer": writer_check.model_dump(), "verification": verification.model_dump(),
            "policy_denied": [{"tool": d["tool"], "args": d["args"], "reason": d["reason"]} for d in denied],
            "data_as_of": as_of,
        }
        detail: dict[str, Any] = {"mode": writer_check.mode, "attempts": writer_check.attempts,
                                  "unsupported": writer_check.unsupported, "checked": verification.checked,
                                  "matched": verification.matched, "report_unsupported": verification.unsupported,
                                  "charts": {"price": len(charts.price), "volatility": len(charts.volatility)}}
        if error:
            detail["error"] = error
        steps = [_step("writer", t, r, **detail, attempt=i + 1) for i, r in enumerate(resps)] or \
            [_step("writer", t, **detail)]
        return {"report": report, "status": "safe_fallback" if fell_back else "ok",
                "notes": [f"writer: {error}"] if error else [], "steps": steps}

    # ------------------------------------------------------------------ edges

    def after_guard(state: ReportState) -> str:
        return "refuse" if state.get("guard_category") else "planner"

    def fan_out(state: ReportState) -> list[Send] | str:
        if state.get("guard_category"):
            return "refuse"
        assets = state.get("assets") or []
        if not assets or state.get("status") in ("clarify", "out_of_scope"):
            return END
        shared = {"question": state["question"], "window": state["window"], "scopes": state.get("scopes"),
                  "opts": state.get("opts"), "request_id": state.get("request_id")}
        sends = [Send("asset_agent", {**shared, "asset": a, "index": i}) for i, a in enumerate(assets)]
        if len(assets) >= 2:
            sends.append(Send("comparer", {**shared, "assets": assets}))
        return sends

    g = StateGraph(ReportState)
    g.add_node("input_guard", input_guard)
    g.add_node("refuse", refuse)
    g.add_node("planner", planner)
    g.add_node("asset_agent", asset_agent)
    g.add_node("comparer", comparer)
    g.add_node("writer", writer)
    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", after_guard, ["refuse", "planner"])
    g.add_conditional_edges("planner", fan_out, ["asset_agent", "comparer", "refuse", END])
    g.add_edge("asset_agent", "writer")
    g.add_edge("comparer", "writer")
    g.add_edge("refuse", END)
    g.add_edge("writer", END)
    return g.compile()


__all__ = ["COMPARE_ID", "ReportState", "VerificationReport", "asset_calls", "build_report_graph"]
