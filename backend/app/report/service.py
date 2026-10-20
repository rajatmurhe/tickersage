"""Runs the report graph on the chat agent's dependencies, streams progress and records the trace."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from ..agent import Agent
from ..planner import Planner
from ..router import Router
from ..schemas import Citation, StepTrace, Trace, VerificationReport
from ..tools.registry import RunOptions

from .graph import ReportState, build_report_graph
from .models import AssetSection, Charts, Report, SectionCheck
from .render import report_html

STEP_LABELS = {
    "budget_guard": "Daily LLM budget reached: writing offline",
    "input_guard": "Checking the request",
    "refuse": "Declining the request",
    "planner": "Splitting the request into per-asset tasks",
    "asset_agent": "Researching an asset",
    "comparer": "Comparing the assets side by side",
    "writer": "Writing the summary, checking every number, drawing charts",
}


class ReportService:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.graph = build_report_graph(agent.deps)
        self._offline_graph = None

    def _offline(self):
        """The same graph without an LLM: rules router, template sections and summary."""
        if self._offline_graph is None:
            d = self.agent.deps
            self._offline_graph = build_report_graph(replace(
                d, llm=None, router=Router(d.registry, None), planner=Planner(None, d.planner.mode), semantic_cache=None,
            ))
        return self._offline_graph

    async def stream(self, question: str, opts: RunOptions | None = None, request_id: str | None = None,
                     scopes: list[str] | None = None) -> AsyncIterator[dict[str, Any]]:
        """Yields {"type": "step"} as nodes finish (one per asset sub-agent), then {"type": "final", "report"}."""
        rid = request_id or uuid.uuid4().hex[:16]
        started = time.perf_counter()
        graph, steps = self.graph, []
        budget_step = await self.agent._budget_guard()
        if budget_step is not None:
            graph, steps = self._offline(), [budget_step]
            yield {"type": "step", "node": budget_step.name, "label": STEP_LABELS[budget_step.name],
                   "detail": budget_step.model_dump()}
        granted = sorted(self.agent.deps.policy.for_request(scopes).scopes)
        init: ReportState = {"question": question, "request_id": rid, "opts": opts or RunOptions(), "scopes": granted,
                             "steps": steps}
        final: ReportState | None = None
        async for mode, chunk in graph.astream(init, stream_mode=["updates", "values"]):
            if mode == "updates":
                for node, update in chunk.items():
                    node_steps = (update or {}).get("steps") or []
                    detail = node_steps[-1].model_dump() if node_steps else {}
                    label = STEP_LABELS.get(node, node)
                    asset = (detail.get("detail") or {}).get("asset")
                    if node == "asset_agent" and asset:
                        label = f"Sub-agent researched {asset} and wrote its section"
                    yield {"type": "step", "node": node, "label": label, "detail": detail}
            else:
                final = chunk
        assert final is not None
        report = build_report(final["question"], rid, final, (time.perf_counter() - started) * 1000)
        traces = self.agent.traces
        if traces:
            await traces.save(final["question"], report.trace)
            await traces.save_audit(rid, granted, final.get("policy_decisions", []))
        yield {"type": "final", "report": report, "state": final}

    async def run(self, question: str, opts: RunOptions | None = None, request_id: str | None = None,
                  scopes: list[str] | None = None) -> Report:
        report: Report | None = None
        async for event in self.stream(question, opts, request_id, scopes):
            if event["type"] == "final":
                report = event["report"]
        assert report is not None
        return report


def build_report(question: str, rid: str, st: ReportState, latency_ms: float) -> Report:
    steps: list[StepTrace] = st.get("steps", [])
    results = st.get("results", [])
    r = st.get("report") or {}
    verification = VerificationReport(**r["verification"]) if r.get("verification") else None
    status = st.get("status", "ok")
    trace = Trace(
        request_id=rid,
        intent=None,
        tools=[{"id": x.id, "tool": x.tool, "args": x.args, "ok": x.ok, "error": x.error.code if x.error else None,
                "error_message": x.error.message if x.error else None, "latency_ms": x.latency_ms,
                "cached": x.cached, "source": x.source} for x in sorted(results, key=lambda x: x.id)],
        steps=steps,
        input_tokens=sum(s.input_tokens for s in steps),
        output_tokens=sum(s.output_tokens for s in steps),
        cost_usd=round(sum(s.cost_usd for s in steps), 6),
        latency_ms=round(latency_ms, 1),
        models=list(dict.fromkeys(s.model for s in steps if s.model)),
        fell_back=any(s.fell_back for s in steps) or any("llm unavailable" in n for n in st.get("notes", [])),
        status=status,  # type: ignore[arg-type]
        refusal_reason=st["notes"][0] if status == "refused" and st.get("notes") else None,
        faithfulness=verification.faithfulness if verification else None,
        unsupported_numbers=verification.unsupported if verification else [],
    )
    route = st.get("route")
    if route and route.intent in ("advice", "trade"):
        trace.intent = route.intent
    elif st.get("guard_category") in ("advice", "trade"):
        trace.intent = st["guard_category"]  # type: ignore[typeddict-item]
    report = Report(
        request_id=rid,
        question=question,
        status=status,  # type: ignore[arg-type]
        title=r.get("title", ""),
        window=st.get("window") if r else None,  # type: ignore[arg-type]
        assets=st.get("assets", []) if r else [],
        message=st.get("message"),
        summary=r.get("summary", ""),
        sections=[AssetSection(**s) for s in r.get("sections", [])],
        comparison=r.get("comparison"),
        risks=r.get("risks", []),
        citations=[Citation(**c) for c in r.get("citations", [])],
        charts=Charts(**r["charts"]) if r.get("charts") else Charts(),
        markdown=r.get("markdown") or st.get("message") or "",
        writer=SectionCheck(**r["writer"]) if r.get("writer") else None,
        verification=verification,
        policy_denied=r.get("policy_denied", []),
        notes=st.get("notes", []),
        data_as_of=r.get("data_as_of"),
        trace=trace,
    )
    if r:
        report.html = report_html(report.title, r["subtitle"], r["body"], report.charts, report.citations,
                                  r.get("denial"))
    return report
