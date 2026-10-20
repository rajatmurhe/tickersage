"""Agent service: builds dependencies, runs the graph, streams progress, records traces."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import httpx

from .cache import build_cache
from .config import Settings, get_settings
from .embeddings import HashingEmbedder, OpenAIEmbedder
from .graph import AgentState, Deps, build_graph
from .llm import LLMClient
from .planner import Planner
from .policy import Policy
from .router import Router
from .schemas import ChatResponse, StepTrace, Trace
from .semantic_cache import SemanticCache
from .symbols import SymbolRegistry
from .templates import build_citations
from .tools.docs import DocsIndex
from .tools.market import MarketData
from .tools.news import NewsClient
from .tools.registry import RunOptions, Toolbox
from .tracing import TraceStore

log = logging.getLogger(__name__)

STEP_LABELS = {
    "budget_guard": "Daily LLM budget reached: answering offline",
    "input_guard": "Checking the question",
    "refuse": "Declining the request",
    "router": "Classifying intent",
    "redirect": "Out of scope",
    "cache_lookup": "Checking answer cache",
    "planner": "Planning tool calls",
    "executor": "Fetching market data",
    "synthesizer": "Writing the answer",
    "verifier": "Verifying every number",
    "safe_answer": "Falling back to raw figures",
    "output_guard": "Final safety check",
}


class Agent:
    def __init__(self, settings: Settings, deps: Deps, http: httpx.AsyncClient | None, traces: TraceStore | None):
        self.settings = settings
        self.deps = deps
        self.http = http
        self.traces = traces
        self.graph = build_graph(deps)
        self._offline_graph = None
        if settings.daily_budget_usd is not None and traces is None:
            log.warning("DAILY_BUDGET_USD is set but traces are not stored, so spend can't be capped")

    @classmethod
    async def create(
        cls,
        settings: Settings | None = None,
        *,
        llm: LLMClient | None = None,
        store_traces: bool = True,
        http: httpx.AsyncClient | None = None,
    ) -> Agent:
        s = settings or get_settings()
        http = http or httpx.AsyncClient(headers={"User-Agent": "TickerSage/0.1 (read-only research agent)"})
        cache = build_cache(s.cache_enabled, s.redis_url)
        registry = SymbolRegistry()
        market = MarketData(s, http, registry)
        if s.tool_mode == "replay":
            registry.loader = _fixture_symbol_loader(s)
        else:
            registry.loader = _recording_symbol_loader(s, market) if s.tool_mode == "record" else market.load_exchange_symbols
        embedder = HashingEmbedder()  # the semantic answer cache's threshold is calibrated on this one
        docs = DocsIndex(s.docs_dir, _docs_embedder(s), s.docs_backend, s.pgvector_url,
                         reranker=s.rerank_provider, mode=s.retrieval_mode, cache_dir=s.rag_cache_dir,
                         model_cache_dir=str(s.model_cache_dir))
        await docs.build()
        await registry.ensure_loaded()  # the ~3.7 MB symbol whitelist; otherwise the first request pays for it
        toolbox = Toolbox(
            market=market, news=NewsClient(s, http), docs=docs, registry=registry, cache=cache,
            mode=s.tool_mode, fixtures_dir=s.fixtures_dir, max_news=s.max_news, max_doc_chunks=s.max_doc_chunks,
        )
        if llm is None and s.llm_live:
            llm = LLMClient(s)
        deps = Deps(
            settings=s,
            registry=registry,
            toolbox=toolbox,
            router=Router(registry, llm),
            planner=Planner(llm, s.planner_mode),
            llm=llm,
            semantic_cache=SemanticCache(embedder, s.semantic_cache_threshold) if s.semantic_cache_enabled else None,
            policy=Policy.from_settings(s),
        )
        traces = TraceStore(s.database_url) if store_traces else None
        return cls(s, deps, http, traces)

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()

    @property
    def llm_mode(self) -> str:
        return "live" if self.deps.llm and self.deps.llm.available else "offline"

    # --------------------------------------------------------------- running

    async def stream(
        self, question: str, opts: RunOptions | None = None, request_id: str | None = None,
        scopes: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yields {"type": "step"} events as nodes finish, then {"type": "final", "response": ...}.

        `scopes` narrows the server's default capability scopes for this request (None keeps them all).
        """
        rid = request_id or uuid.uuid4().hex[:16]
        started = time.perf_counter()
        graph, steps = self.graph, []
        budget_step = await self._budget_guard()
        if budget_step is not None:
            graph, steps = self._offline(), [budget_step]
            yield {"type": "step", "node": budget_step.name, "label": STEP_LABELS[budget_step.name],
                   "detail": budget_step.model_dump()}
        granted = sorted(self.deps.policy.for_request(scopes).scopes)
        init: AgentState = {"question": question, "request_id": rid, "opts": opts or RunOptions(), "scopes": granted,
                            "steps": steps}
        final: AgentState | None = None
        async for mode, chunk in graph.astream(init, stream_mode=["updates", "values"]):
            if mode == "updates":
                for node, update in chunk.items():
                    steps = (update or {}).get("steps") or []
                    yield {"type": "step", "node": node, "label": STEP_LABELS.get(node, node),
                           "detail": steps[-1].model_dump() if steps else {}}
            else:
                final = chunk
        assert final is not None
        question = final["question"]  # with any pasted secret redacted by input_guard
        response = self._response(question, rid, final, (time.perf_counter() - started) * 1000)
        if self.traces:
            await self.traces.save(question, response.trace)
            await self.traces.save_audit(rid, granted, final.get("policy_decisions", []))
        yield {"type": "final", "response": response, "state": final}

    async def ask(self, question: str, opts: RunOptions | None = None, request_id: str | None = None,
                  scopes: list[str] | None = None) -> ChatResponse:
        response: ChatResponse | None = None
        async for event in self.stream(question, opts, request_id, scopes):
            if event["type"] == "final":
                response = event["response"]
        assert response is not None
        return response

    async def _budget_guard(self) -> StepTrace | None:
        """A trace step when today's recorded LLM cost has reached DAILY_BUDGET_USD, else None.

        Spend is read from the trace table, so the cap holds across restarts when traces are in
        Postgres. It is a soft cap: requests already running when it is reached still finish live.
        """
        budget = self.settings.daily_budget_usd
        if budget is None or self.traces is None or self.llm_mode != "live":
            return None
        started = time.perf_counter()
        try:
            spent = await self.traces.cost_today()
        except Exception:
            log.exception("could not read today's LLM spend; answering offline to stay under budget")
            spent = None
        if spent is not None and spent < budget:
            return None
        return StepTrace(name="budget_guard", latency_ms=round((time.perf_counter() - started) * 1000, 1),
                         fell_back=True, detail={"daily_budget_usd": budget, "spent_today_usd": spent,
                                                 "llm": "offline"})

    def _offline(self):
        """The same graph without an LLM: rules router and planner, template answers.

        It has no semantic cache, so template answers never fill the cache that live answers use.
        """
        if self._offline_graph is None:
            d = self.deps
            self._offline_graph = build_graph(replace(
                d, llm=None, router=Router(d.registry, None), planner=Planner(None, d.planner.mode),
                semantic_cache=None,
            ))
        return self._offline_graph

    def _response(self, question: str, rid: str, st: AgentState, latency_ms: float) -> ChatResponse:
        results = st.get("results", [])
        steps = st.get("steps", [])
        route = st.get("route")
        verification = st.get("verification")
        citations, _, _ = build_citations(results)
        market = [r for r in results if r.ok and r.as_of and r.tool != "search_docs"]
        trace = Trace(
            request_id=rid,
            intent=route.intent if route else None,
            tools=[
                {"id": r.id, "tool": r.tool, "args": r.args, "ok": r.ok, "error": r.error.code if r.error else None,
                 "error_message": r.error.message if r.error else None, "latency_ms": r.latency_ms,
                 "cached": r.cached, "source": r.source}
                for r in results
            ],
            steps=steps,
            input_tokens=sum(s.input_tokens for s in steps),
            output_tokens=sum(s.output_tokens for s in steps),
            cost_usd=round(sum(s.cost_usd for s in steps), 6),
            latency_ms=round(latency_ms, 1),
            models=list(dict.fromkeys(s.model for s in steps if s.model)),
            fell_back=any(s.fell_back for s in steps) or any("llm unavailable" in n for n in st.get("notes", [])),
            status=st.get("status", "ok"),  # type: ignore[arg-type]
            refusal_reason=st.get("refusal_reason"),
            faithfulness=verification.faithfulness if verification else None,
            unsupported_numbers=verification.unsupported if verification else [],
        )
        if st.get("guard_category") and trace.intent is None:
            trace.intent = st["guard_category"] if st["guard_category"] in ("advice", "trade") else None  # type: ignore[assignment]
        return ChatResponse(
            request_id=rid,
            question=question,
            answer=st.get("answer") or "",
            status=trace.status,
            intent=trace.intent,
            citations=citations,
            data_as_of=min((r.as_of for r in market), default=None),  # type: ignore[type-var]
            trace=trace,
        )


def _docs_embedder(s: Settings):
    if s.embedding_provider == "local":
        from .embeddings import LocalEmbedder

        return LocalEmbedder(s.embedding_model, cache_dir=str(s.model_cache_dir))
    if s.embedding_provider == "openai" and s.openai_api_key:
        return OpenAIEmbedder(s.openai_api_key)
    return HashingEmbedder()


def _fixture_symbol_loader(s: Settings):
    import json

    async def load() -> list[dict]:
        path = s.fixtures_dir / "exchange_symbols.json"
        return json.loads(path.read_text()) if path.exists() else []

    return load


def _recording_symbol_loader(s: Settings, market: MarketData):
    import json

    async def load() -> list[dict]:
        symbols = await market.load_exchange_symbols()
        path = s.fixtures_dir / "exchange_symbols.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(symbols, separators=(",", ":")))
        return symbols

    return load
