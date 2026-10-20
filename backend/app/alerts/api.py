"""Alerts and watchlist endpoints.

  POST   /api/alerts/compile                 text (+ answers) -> spec, clarifying questions or a refusal
  GET    /api/alerts/rules                   this client's rules
  POST   /api/alerts/rules                   save a compiled rule (the spec is re-checked against the text)
  POST   /api/alerts/rules/{id}/enabled      pause or resume a rule
  POST   /api/alerts/rules/{id}/check        check a rule now (it fires only if the scheduler would)
  DELETE /api/alerts/rules/{id}
  GET    /api/alerts/events                  the alert feed, newest first
  POST   /api/alerts/events/read             mark alerts read
  GET    /api/alerts/status                  scheduler and channel status, unread count
  GET/POST/DELETE /api/watchlist             the assets this client follows

Rules belong to the client id the browser sends in X-Client-Id (a random id kept in local storage), so on a
shared demo each visitor sees only their own. Nothing here can trade: the only tools a check can reach are the
read-only market, ticker and news tools, through the policy engine.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ..tools.errors import ToolError
from .compiler import AlertCompileResult, AlertCompiler, screen, spec_numbers_grounded
from .notify import configured
from .parser import FIELDS
from .spec import DISCLAIMER, AlertSpec, summarize

router = APIRouter(tags=["alerts"])

DEFAULT_OWNER = "local"


async def rate_limit(request: Request) -> None:
    limiter = getattr(request.app.state, "limiter", None)
    if limiter is not None:
        await limiter.check(request)


def owner_id(x_client_id: str | None = Header(default=None)) -> str:
    if x_client_id is None or not x_client_id.strip():
        return DEFAULT_OWNER
    cid = x_client_id.strip()
    if not (8 <= len(cid) <= 64) or not all(ch.isalnum() or ch in "-_" for ch in cid):
        raise HTTPException(400, "X-Client-Id must be 8-64 letters, digits, '-' or '_'")
    return cid


def _service(request: Request):
    service = getattr(request.app.state, "alerts", None)
    if service is None:
        raise HTTPException(503, "alerts are unavailable: the alert store couldn't be opened")
    return service


def _is_admin(request: Request, token: str | None) -> bool:
    admin = request.app.state.agent.settings.admin_token
    return not admin or bool(token and secrets.compare_digest(token.encode(), admin.encode()))


# ------------------------------------------------------------------ compile and rules


class CompileRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
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


class CreateRule(BaseModel):
    text: str = Field(min_length=1, max_length=1500, description="`text` from the compile result")
    spec: AlertSpec
    channels: list[Literal["in_app", "telegram", "email"]] = Field(default_factory=lambda: ["in_app"])
    source: Literal["rules", "llm"] = "rules"


class EnabledBody(BaseModel):
    enabled: bool


class ReadBody(BaseModel):
    ids: list[int] | None = Field(default=None, description="Omit to mark every alert read")


def _rule_out(r: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "created_at", "text", "spec", "summary", "channels", "source", "enabled", "armed",
            "last_checked_at", "last_fired_at", "last_action", "last_error", "fire_count")
    return {k: r.get(k) for k in keys}


@router.post("/api/alerts/compile", response_model=AlertCompileResult, dependencies=[Depends(rate_limit)])
async def compile_alert(body: CompileRequest, request: Request) -> AlertCompileResult:
    agent = request.app.state.agent
    llm = agent.deps.llm
    if llm is not None and await agent._budget_guard() is not None:
        llm = None  # today's LLM budget is spent: the rules parser compiles offline
    compiler = AlertCompiler(agent.deps.registry, llm)
    return await compiler.compile(body.text, body.answers, request_id=uuid.uuid4().hex[:16])


@router.get("/api/alerts/rules")
async def list_rules(request: Request, owner: str = Depends(owner_id)) -> list[dict[str, Any]]:
    return [_rule_out(r) for r in await _service(request).store.rules(owner)]


@router.post("/api/alerts/rules", dependencies=[Depends(rate_limit)])
async def create_rule(body: CreateRule, request: Request, owner: str = Depends(owner_id),
                      x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    service = _service(request)
    if hit := screen([body.text]):
        raise HTTPException(422, f"refused ({hit[0]}): alerts only watch market data")
    problems = spec_numbers_grounded(body.spec, body.text)
    if problems:
        raise HTTPException(422, "the spec has values that aren't in the alert's text: " + ", ".join(problems))
    registry = request.app.state.agent.deps.registry
    try:
        await registry.ensure_loaded()
        symbol = registry.base(registry.resolve(body.spec.symbol))
    except ToolError as exc:
        raise HTTPException(422, exc.message) from exc
    external = [c for c in body.channels if c != "in_app"]
    if external and not _is_admin(request, x_admin_token):
        raise HTTPException(403, "Telegram and email go to the server owner's chat and inbox: send the admin token "
                                 "in X-Admin-Token to use them")
    limit = service.settings.alerts_max_rules_per_owner
    if await service.store.count_rules(owner) >= limit:
        raise HTTPException(429, f"limit of {limit} alert rules reached: delete one first")
    spec = body.spec.model_copy(update={"symbol": symbol})
    channels = list(dict.fromkeys(["in_app", *body.channels]))
    rule = await service.store.add_rule(owner, body.text, spec.model_dump(), summarize(spec), channels, body.source)
    return _rule_out(rule)


async def _owned(request: Request, rule_id: str, owner: str) -> dict[str, Any]:
    rule = await _service(request).store.rule(rule_id, owner)
    if rule is None:
        raise HTTPException(404, "rule not found")
    return rule


@router.post("/api/alerts/rules/{rule_id}/enabled")
async def set_enabled(rule_id: str, body: EnabledBody, request: Request,
                      owner: str = Depends(owner_id)) -> dict[str, Any]:
    await _owned(request, rule_id, owner)
    values: dict[str, Any] = {"enabled": body.enabled}
    if body.enabled:
        values["armed"] = True  # a resumed rule starts fresh
    await _service(request).store.update_rule(rule_id, **values)
    return _rule_out(await _owned(request, rule_id, owner))


@router.post("/api/alerts/rules/{rule_id}/check", dependencies=[Depends(rate_limit)])
async def check_now(rule_id: str, request: Request, owner: str = Depends(owner_id)) -> dict[str, Any]:
    rule = await _owned(request, rule_id, owner)
    out = await _service(request).check_rule(rule)
    out["rule"] = _rule_out(await _owned(request, rule_id, owner))
    return out


@router.delete("/api/alerts/rules/{rule_id}")
async def delete_rule(rule_id: str, request: Request, owner: str = Depends(owner_id)) -> dict[str, bool]:
    if not await _service(request).store.delete_rule(rule_id, owner):
        raise HTTPException(404, "rule not found")
    return {"deleted": True}


# ------------------------------------------------------------------ the feed


@router.get("/api/alerts/events")
async def list_events(request: Request, owner: str = Depends(owner_id), limit: int = 50, unread_only: bool = False,
                      rule_id: str | None = None) -> list[dict[str, Any]]:
    return await _service(request).store.events(owner, min(max(limit, 1), 200), unread_only, rule_id)


@router.post("/api/alerts/events/read")
async def mark_read(body: ReadBody, request: Request, owner: str = Depends(owner_id)) -> dict[str, int]:
    return {"marked": await _service(request).store.mark_read(owner, body.ids)}


@router.get("/api/alerts/status")
async def status(request: Request, owner: str = Depends(owner_id)) -> dict[str, Any]:
    service = _service(request)
    s = service.settings
    return {"scheduler": service.running, "interval_s": s.alerts_check_interval_s, "last_run": service.last_run,
            "channels": configured(s), "external_channels_need_admin": bool(s.admin_token),
            "unread": await service.store.unread(owner), "max_rules": s.alerts_max_rules_per_owner,
            "disclaimer": DISCLAIMER, "now": datetime.now().astimezone().isoformat()}


# ------------------------------------------------------------------ watchlist


class WatchBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)


@router.get("/api/watchlist")
async def get_watchlist(request: Request, owner: str = Depends(owner_id)) -> list[dict[str, Any]]:
    service = _service(request)
    items = await service.store.watchlist(owner)
    rules = await service.store.rules(owner)
    for item in items:
        item["rules"] = sum(1 for r in rules if r["spec"].get("symbol") == item["symbol"])
    return items


@router.post("/api/watchlist")
async def add_to_watchlist(body: WatchBody, request: Request, owner: str = Depends(owner_id)) -> dict[str, Any]:
    service = _service(request)
    registry = request.app.state.agent.deps.registry
    try:
        await registry.ensure_loaded()
        symbol = registry.base(registry.resolve(body.symbol))
    except ToolError as exc:
        raise HTTPException(422, exc.message) from exc
    if len(await service.store.watchlist(owner)) >= 50 and symbol not in \
            {i["symbol"] for i in await service.store.watchlist(owner)}:
        raise HTTPException(429, "the watchlist is limited to 50 assets")
    added = await service.store.watch(owner, symbol)
    return {"symbol": symbol, "added": added}


@router.delete("/api/watchlist/{symbol}")
async def remove_from_watchlist(symbol: str, request: Request, owner: str = Depends(owner_id)) -> dict[str, bool]:
    return {"removed": await _service(request).store.unwatch(owner, symbol.upper())}

