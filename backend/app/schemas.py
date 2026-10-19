"""Shared request, routing, planning and trace schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Intent = Literal["price", "metrics", "compare", "explain_move", "concept", "out_of_scope", "advice", "trade"]
Window = Literal["24h", "7d", "30d"]
ToolName = Literal[
    "get_ticker",
    "get_klines",
    "get_order_book",
    "compute_metrics",
    "compare_assets",
    "get_news_sentiment",
    "search_docs",
    "attribute_move",
]
Status = Literal["ok", "refused", "out_of_scope", "clarify", "safe_fallback", "error"]


class RouteDecision(BaseModel):
    intent: Intent
    symbols: list[str] = Field(default_factory=list, description="Base assets, e.g. ['BTC', 'ETH']")
    window: Window | None = None
    wants_order_book: bool = False
    topic: str | None = None
    source: Literal["rules", "llm"] = "rules"


class ToolCall(BaseModel):
    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)


class ToolPlan(BaseModel):
    calls: list[ToolCall] = Field(default_factory=list, max_length=8)
    note: str | None = None


class ToolErrorInfo(BaseModel):
    code: Literal[
        "unknown_symbol", "timeout", "upstream_error", "empty_data", "invalid_input", "fixture_missing"
    ]
    message: str


class ToolResult(BaseModel):
    id: int
    tool: ToolName
    args: dict[str, Any]
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolErrorInfo | None = None
    source: str | None = None
    as_of: datetime | None = None
    latency_ms: float = 0.0
    cached: bool = False


class Citation(BaseModel):
    id: int
    kind: Literal["data", "doc", "news"]
    title: str
    url: str | None = None


class StepTrace(BaseModel):
    name: str
    latency_ms: float
    model: str | None = None
    provider: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    fell_back: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


class VerificationReport(BaseModel):
    checked: int = 0
    matched: int = 0
    unsupported: list[str] = Field(default_factory=list)
    attempts: int = 0

    @property
    def faithfulness(self) -> float:
        return 1.0 if self.checked == 0 else self.matched / self.checked


class Trace(BaseModel):
    request_id: str
    intent: Intent | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[StepTrace] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    models: list[str] = Field(default_factory=list)
    fell_back: bool = False
    status: Status = "ok"
    refusal_reason: str | None = None
    faithfulness: float | None = None
    unsupported_numbers: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class ChatResponse(BaseModel):
    request_id: str
    question: str
    answer: str
    status: Status
    intent: Intent | None = None
    citations: list[Citation] = Field(default_factory=list)
    data_as_of: datetime | None = None
    trace: Trace
