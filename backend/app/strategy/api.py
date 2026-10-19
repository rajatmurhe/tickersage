"""Strategy Studio endpoints. Kept apart from the chat graph: the chat's guardrails still refuse trade and
advice requests exactly as before, and nothing here can place an order.

  POST /api/strategy/compile   idea (+ answers) -> spec, clarifying questions, lint findings, Mermaid diagram
  POST /api/strategy/backtest  spec -> lint gate, then a backtest on Binance klines (get_klines, so replay works)

The backtest's candle fetch goes through the same policy engine as the chat's tool calls, with only the
market:read scope, and its decision is written to the audit log (GET /api/audit?request_id=...).
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from ..policy import Policy
from ..schemas import ToolCall
from ..tools.registry import Toolbox
from ..tracing import TraceStore
from .backtest import BacktestError, BacktestResult, candles_frame, run_backtest
from .compiler import FIELDS, REJECTIONS, CompileResult, StrategyCompiler, screen
from .linter import Finding, has_errors, lint
from .spec import CANDLES, DISCLAIMER, StrategySpec, summarize

router = APIRouter(prefix="/api/strategy", tags=["strategy"])

SCOPES = ["market:read"]  # a backtest only reads candles


async def rate_limit(request: Request) -> None:
    """The same per-IP limiter as /api/chat (a compile can call the LLM; a backtest can call Binance)."""
    limiter = getattr(request.app.state, "limiter", None)
    if limiter is not None:
        await limiter.check(request)


class CompileRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    answers: dict[str, str] = Field(default_factory=dict, description="Answers to clarifying questions, by field")

    @field_validator("answers")
    @classmethod
    def _answers(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = set(v) - set(FIELDS)
        if unknown:
            raise ValueError(f"unknown answer fields: {sorted(unknown)}")
        if any(len(a) > 200 for a in v.values()):
            raise ValueError("answers are limited to 200 characters")
        return v


class BacktestRequest(BaseModel):
    spec: StrategySpec
    text: str | None = Field(default=None, max_length=2000, description="The idea as compiled (lint_text), for "
                                                                          "the checks that compare spec and text")


class BacktestResponse(BaseModel):
    status: Literal["ok", "blocked", "error"]
    findings: list[Finding] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)
    result: BacktestResult | None = None
    error: str | None = None
    request_id: str | None = Field(default=None, description="Finds this backtest's data call in /api/audit")
    disclaimer: str = DISCLAIMER


@router.post("/compile", response_model=CompileResult, dependencies=[Depends(rate_limit)])
async def compile_strategy(body: CompileRequest, request: Request) -> CompileResult:
    agent = request.app.state.agent
    llm = agent.deps.llm
    if llm is not None and await agent._budget_guard() is not None:
        llm = None  # today's LLM budget is spent: the rules parser compiles offline
    compiler = StrategyCompiler(agent.deps.registry, llm)
    return await compiler.compile(body.text, body.answers, request_id=uuid.uuid4().hex[:16])


@router.post("/backtest", response_model=BacktestResponse, dependencies=[Depends(rate_limit)])
async def backtest_strategy(body: BacktestRequest, request: Request) -> BacktestResponse:
    agent = request.app.state.agent
    return await backtest(body.spec, body.text, agent.deps.toolbox, agent.deps.policy, traces=agent.traces)


async def backtest(spec: StrategySpec, text: str | None, toolbox: Toolbox, policy: Policy, *,
                   traces: TraceStore | None = None, request_id: str | None = None) -> BacktestResponse:
    """Lint gate, then candles through get_klines (checked by the policy engine and audited like a chat tool
    call; cached live, fixtures in replay), then the backtest."""
    rid = request_id or f"bt-{uuid.uuid4().hex[:16]}"
    summary = summarize(spec)
    if text and (hit := screen([text])):
        return BacktestResponse(status="blocked", summary=summary, error=REJECTIONS[hit[0]], request_id=rid)
    findings = lint(spec, text)
    if has_errors(findings):
        return BacktestResponse(status="blocked", findings=findings, summary=summary, request_id=rid,
                                error="The linter found errors in these rules. Fix them before backtesting.")
    call = ToolCall(tool="get_klines", args={"symbol": spec.symbol, "interval": spec.interval, "limit": CANDLES})
    granted = policy.for_request(SCOPES)
    decision = granted.check(call.tool, dict(call.args))
    if traces is not None:
        await traces.save_audit(rid, sorted(granted.scopes), [decision.as_dict()])
    if not decision.allowed:
        return BacktestResponse(status="blocked", findings=findings, summary=summary, request_id=rid,
                                error="The candles weren't fetched because this request's permissions don't "
                                      f"allow it: get_klines {decision.reason}.")
    candles = await toolbox.call(call, 0)
    if not candles.ok or candles.data is None:
        message = candles.error.message if candles.error else "The candle data couldn't be loaded."
        return BacktestResponse(status="error", findings=findings, summary=summary, error=message,
                                request_id=rid)
    try:
        df = candles_frame(candles.data, spec.interval)
        result = run_backtest(spec, df, source=candles.source, as_of=candles.as_of)
    except BacktestError as exc:
        return BacktestResponse(status="error", findings=findings, summary=summary, error=str(exc),
                                request_id=rid)
    return BacktestResponse(status="ok", findings=findings, summary=summary, result=result, request_id=rid)
