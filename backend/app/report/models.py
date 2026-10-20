"""Report request and response shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..schemas import Citation, Scope, Status, Trace, VerificationReport, Window


class ReportRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    # Narrows what the sub-agents' tool calls may touch; omitted means the server's default scopes.
    scopes: list[Scope] | None = None


class SectionCheck(BaseModel):
    """How one written part of the report fared against the number verifier."""

    mode: Literal["llm", "template"]
    attempts: int = 0
    checked: int = 0
    matched: int = 0
    unsupported: list[str] = Field(default_factory=list)  # in the last LLM draft, when it was rejected
    fell_back: bool = False  # the verifier rejected every LLM draft, so the template was used
    removed: list[str] = Field(default_factory=list)  # sentences the output guard dropped


class AssetSection(BaseModel):
    asset: str  # base asset, e.g. BTC
    name: str  # display name, e.g. Bitcoin
    markdown: str
    check: SectionCheck
    tools: list[dict[str, Any]] = Field(default_factory=list)


class PricePoint(BaseModel):
    t: datetime
    close: float
    index: float  # close rebased to 100 at the first candle


class PriceSeries(BaseModel):
    asset: str
    interval: str
    points: list[PricePoint]
    cite: int | None = None


class VolatilityBar(BaseModel):
    asset: str
    volatility_pct: float  # per candle
    annualized_volatility_pct: float
    interval: str
    cite: int | None = None


class Charts(BaseModel):
    price: list[PriceSeries] = Field(default_factory=list)
    volatility: list[VolatilityBar] = Field(default_factory=list)


class Report(BaseModel):
    request_id: str
    question: str
    status: Status
    title: str = ""
    window: Window | None = None
    assets: list[str] = Field(default_factory=list)
    message: str | None = None  # the reply when there is no report: a refusal, a clarifying question
    summary: str = ""
    sections: list[AssetSection] = Field(default_factory=list)
    comparison: str | None = None
    risks: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    charts: Charts = Field(default_factory=Charts)
    markdown: str = ""
    writer: SectionCheck | None = None
    verification: VerificationReport | None = None  # the whole report body, checked after assembly
    policy_denied: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    data_as_of: datetime | None = None
    trace: Trace
    html: str | None = None
