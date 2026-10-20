"""Checking a rule: the data calls go through the policy engine, the maths is plain Pandas.

Each check proposes the read-only tool calls the rule needs (candles, the 24h ticker, headlines), lets the
policy engine decide which run (scopes market:read and news:read only, audited like a chat request), runs
them through the toolbox (so the cache, replay fixtures and injected faults apply), and evaluates every
condition. A condition whose data is missing is unknown, never false, so a failed fetch neither fires a rule
nor re-arms it.

`decide` is the scheduler's state machine, kept pure so it can be tested on its own: a rule fires when its
conditions become true, waits until they are false again to re-arm, and never fires twice within its cooldown.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from ..policy import Policy
from ..schemas import ToolCall, ToolResult
from ..strategy.spec import fmt_hours, fmt_num
from ..symbols import asset_name
from ..tools.metrics import rsi as last_rsi
from ..tools.registry import RunOptions, Toolbox
from ..tracing import TraceStore
from .spec import (
    CANDLES, NEWS_NEGATIVE, NEWS_POSITIVE, AlertCondition, AlertSpec, describe, window_interval,
)

SCOPES = ["market:read", "news:read"]  # an alert check only reads market data and headlines


class ConditionResult(BaseModel):
    index: int
    kind: str
    description: str
    met: bool | None = Field(description="None when the data couldn't be loaded")
    observed: dict[str, Any] = Field(default_factory=dict, description="The values the check compared")
    detail: str = ""


class Evaluation(BaseModel):
    request_id: str
    symbol: str
    met: bool | None
    conditions: list[ConditionResult]
    errors: list[str] = Field(default_factory=list)
    denied: list[dict[str, Any]] = Field(default_factory=list)
    as_of: datetime | None = None
    sources: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict, description="Per-condition state to keep for the next check")


@dataclass
class Checked:
    evaluation: Evaluation
    results: list[ToolResult] = field(default_factory=list)


# ------------------------------------------------------------------ tool calls


def _klines_call(symbol: str, interval: str) -> ToolCall:
    return ToolCall(tool="get_klines", args={"symbol": symbol, "interval": interval, "limit": CANDLES})


def calls_for(spec: AlertSpec) -> list[ToolCall]:
    """The distinct read-only calls a check needs, in condition order."""
    calls: list[ToolCall] = []

    def add(call: ToolCall) -> None:
        if all(c.tool != call.tool or c.args != call.args for c in calls):
            calls.append(call)

    for c in spec.conditions:
        if c.kind in ("rsi", "price_vs_sma", "sma_cross"):
            add(_klines_call(spec.symbol, c.interval))
        elif c.kind == "price_change":
            iv = window_interval(c.window_hours)
            if iv is not None:
                add(_klines_call(spec.symbol, iv[0]))
        elif c.kind == "price_level":
            add(ToolCall(tool="get_ticker", args={"symbol": spec.symbol}))
        else:
            add(ToolCall(tool="get_news_sentiment", args={"query": asset_name(spec.symbol)}))
    return calls


# ------------------------------------------------------------------ maths


def _closes(data: dict[str, Any]) -> pd.Series:
    candles = data.get("candles") or []
    return pd.Series([float(c["close"]) for c in candles], dtype=float)


def _r(x: float, nd: int = 2) -> float:
    return round(float(x), nd)


def _price_round(x: float) -> float:
    return round(x, 2) if abs(x) >= 1 else round(x, 6)


def _side(value: float, level: float) -> int:
    return (value > level) - (value < level)


def _evaluate(i: int, c: AlertCondition, data: dict[str, Any] | None, state: dict[str, Any]) -> ConditionResult:
    res = ConditionResult(index=i, kind=c.kind, description=describe(c), met=None)
    if data is None:
        res.detail = "data unavailable"
        return res
    if c.kind in ("rsi", "price_vs_sma", "sma_cross", "price_change"):
        close = _closes(data)
        if c.kind == "rsi":
            value = last_rsi(close, c.period)
            if value is None:
                res.detail = f"not enough candles for RSI({c.period})"
                return res
            res.observed = {"rsi": _r(value), "threshold": c.value, "interval": c.interval, "period": c.period}
            res.met = value < c.value if c.op == "below" else value > c.value
            res.detail = f"RSI({c.period}) on {c.interval} candles is {fmt_num(_r(value))}"
            return res
        if c.kind == "price_vs_sma":
            if len(close) < c.period:
                res.detail = f"not enough candles for SMA({c.period})"
                return res
            sma = float(close.rolling(c.period).mean().iloc[-1])
            last = float(close.iloc[-1])
            res.observed = {"close": _price_round(last), "sma": _price_round(sma), "interval": c.interval,
                            "period": c.period}
            res.detail = (f"the {c.interval} close is {fmt_num(_price_round(last))} against an SMA({c.period}) of "
                          f"{fmt_num(_price_round(sma))}")
            if c.op == "crosses":
                return _crossing(res, i, _side(last, sma), state)
            res.met = last < sma if c.op == "below" else last > sma
            return res
        if c.kind == "sma_cross":
            if len(close) < max(c.fast, c.slow):
                res.detail = f"not enough candles for SMA({max(c.fast, c.slow)})"
                return res
            fast = float(close.rolling(c.fast).mean().iloc[-1])
            slow = float(close.rolling(c.slow).mean().iloc[-1])
            res.observed = {"sma_fast": _price_round(fast), "sma_slow": _price_round(slow), "interval": c.interval}
            res.detail = (f"SMA({c.fast}) is {fmt_num(_price_round(fast))} and SMA({c.slow}) is "
                          f"{fmt_num(_price_round(slow))} on {c.interval} candles")
            res.met = fast > slow if c.op == "above" else fast < slow
            return res
        iv = window_interval(c.window_hours)
        if iv is None or len(close) <= iv[1]:
            res.detail = f"can't measure a {fmt_hours(c.window_hours)} window from the candles"
            return res
        start, last = float(close.iloc[-1 - iv[1]]), float(close.iloc[-1])
        change = (last / start - 1) * 100
        res.observed = {"change_pct": _r(change), "from_price": _price_round(start), "to_price": _price_round(last),
                        "window_hours": c.window_hours, "threshold_pct": c.pct}
        res.detail = (f"price moved {change:+.2f}% over {fmt_hours(c.window_hours)} ({fmt_num(_price_round(start))} "
                      f"to {fmt_num(_price_round(last))})")
        res.met = change <= -c.pct if c.direction == "down" else change >= c.pct if c.direction == "up" else \
            abs(change) >= c.pct
        return res
    if c.kind == "price_level":
        price = float(data["last_price"])
        res.observed = {"last_price": price, "level": c.price}
        res.detail = f"the last price is {fmt_num(price)}"
        if c.op == "reaches":
            return _crossing(res, i, _side(price, c.price), state)
        res.met = price > c.price if c.op == "above" else price < c.price
        return res
    scores = [h["sentiment"] for h in data.get("headlines", []) if not h.get("redacted")]
    if not scores:
        res.met = False
        res.detail = "no headlines to score"
        res.observed = {"headlines": 0}
        return res
    avg = sum(scores) / len(scores)
    level = c.threshold if c.threshold is not None else (NEWS_NEGATIVE if c.tone == "negative" else NEWS_POSITIVE)
    res.observed = {"avg_sentiment": round(avg, 3), "headlines": len(scores), "threshold": level}
    res.detail = f"the latest {len(scores)} headlines average a sentiment score of {avg:+.3f}"
    res.met = avg <= level if c.tone == "negative" else avg >= level
    return res


def _crossing(res: ConditionResult, i: int, side: int, state: dict[str, Any]) -> ConditionResult:
    """'reaches' / 'crosses': met once the value is on the other side of the level from the side it was on at
    the first check (or exactly at it). The starting side is kept in the rule's state."""
    key = f"side_{i}"
    start = state.get(key)
    if start is None or start == 0:
        state[key] = side
        start = side
    res.observed["start_side"] = {1: "above", -1: "below", 0: "at"}[start]
    res.met = side == 0 or side != start
    if res.met:
        state.pop(key, None)  # once reached, the next crossing is measured from the new side
    return res


# ------------------------------------------------------------------ a check


async def check(spec: AlertSpec, toolbox: Toolbox, policy: Policy, *, state: dict[str, Any] | None = None,
                traces: TraceStore | None = None, request_id: str | None = None,
                opts: RunOptions | None = None) -> Checked:
    rid = request_id or f"alert-{uuid.uuid4().hex[:16]}"
    state = dict(state or {})
    calls = calls_for(spec)
    granted = policy.for_request(SCOPES)
    decisions = granted.review(calls)
    if traces is not None:
        await traces.save_audit(rid, sorted(granted.scopes), [d.as_dict() for d in decisions])
    results: list[ToolResult] = []
    errors: list[str] = []
    denied: list[dict[str, Any]] = []
    by_call: dict[str, dict[str, Any] | None] = {}
    for i, (call, decision) in enumerate(zip(calls, decisions)):
        key = _key(call)
        if not decision.allowed:
            denied.append(decision.as_dict())
            errors.append(f"{call.tool} was not run: {decision.reason}")
            by_call[key] = None
            continue
        result = await toolbox.call(call, i, opts)
        results.append(result)
        if result.ok and result.data is not None:
            by_call[key] = result.data
        else:
            by_call[key] = None
            errors.append(result.error.message if result.error else f"{call.tool} returned no data")

    conds: list[ConditionResult] = []
    for i, c in enumerate(spec.conditions):
        call = _call_for(spec, c)
        conds.append(_evaluate(i, c, by_call.get(_key(call)) if call else None, state))
    met = _combine([c.met for c in conds], spec.logic)
    ok = [r for r in results if r.ok]
    as_of = max((r.as_of for r in ok if r.as_of), default=None)
    evaluation = Evaluation(request_id=rid, symbol=spec.symbol, met=met, conditions=conds, errors=errors,
                            denied=denied, as_of=as_of, sources=list(dict.fromkeys(r.source for r in ok if r.source)),
                            state=state)
    return Checked(evaluation, results)


def _key(call: ToolCall) -> str:
    return f"{call.tool}:{sorted(call.args.items())}"


def _call_for(spec: AlertSpec, c: AlertCondition) -> ToolCall | None:
    if c.kind in ("rsi", "price_vs_sma", "sma_cross"):
        return _klines_call(spec.symbol, c.interval)
    if c.kind == "price_change":
        iv = window_interval(c.window_hours)
        return _klines_call(spec.symbol, iv[0]) if iv else None
    if c.kind == "price_level":
        return ToolCall(tool="get_ticker", args={"symbol": spec.symbol})
    return ToolCall(tool="get_news_sentiment", args={"query": asset_name(spec.symbol)})


def _combine(values: list[bool | None], logic: str) -> bool | None:
    """all: false if any is false, unknown if any is unknown, else true. any: true if any is true, unknown if
    any is unknown, else false."""
    if logic == "all":
        if any(v is False for v in values):
            return False
        return None if any(v is None for v in values) else True
    if any(v is True for v in values):
        return True
    return None if any(v is None for v in values) else False


# ------------------------------------------------------------------ the firing decision

Action = Literal["fire", "wait_rearm", "rearm", "cooldown", "idle", "error"]


def decide(met: bool | None, armed: bool, last_fired_at: datetime | None, now: datetime,
           cooldown_minutes: int) -> tuple[Action, bool]:
    """(action, armed afterwards).

    error       the data couldn't be read: nothing changes
    idle        not met and already armed
    rearm       not met after firing: the rule can fire again
    wait_rearm  still met since it last fired: it has to go false first (debounce)
    cooldown    met and armed, but it fired less than `cooldown_minutes` ago: it stays armed, so it fires
                on the first check after the cooldown if still met
    fire        met, armed and outside the cooldown
    """
    if met is None:
        return "error", armed
    if not met:
        return ("idle" if armed else "rearm"), True
    if not armed:
        return "wait_rearm", False
    if last_fired_at is not None and now - last_fired_at < timedelta(minutes=cooldown_minutes):
        return "cooldown", True
    return "fire", False

