"""FastAPI app: chat (JSON and SSE streaming), traces, the tool-call audit log, eval results and the static UI.

Run from the repo root:  uvicorn app.main:app --app-dir backend --reload
"""

from __future__ import annotations

import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from .agent import Agent
from .alerts.api import router as alerts_router
from .alerts.service import AlertService
from .alerts.store import AlertStore
from .config import REPO_DIR, get_settings
from .ratelimit import RateLimiter
from .report.api import router as report_router
from .schemas import ChatRequest, ChatResponse
from .strategy.api import router as strategy_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tickersage")

STATIC_DIR = Path(__file__).parent / "static"
EVAL_RESULTS = REPO_DIR / "evals" / "results" / "latest.json"
ANSWER_CHUNK = 24  # characters per streamed answer event


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.agent = await Agent.create(settings)
    app.state.limiter = RateLimiter.from_settings(settings)
    try:
        app.state.alerts = AlertService(app.state.agent, AlertStore(settings.database_url))
    except Exception:
        log.exception("alert store unavailable: /api/alerts will answer 503")
        app.state.alerts = None
    if app.state.alerts is not None and settings.alerts_scheduler:
        app.state.alerts.start()
    log.info("TickerSage ready: llm=%s tools=%s routing=%s", app.state.agent.llm_mode, settings.tool_mode,
             settings.routing_mode)
    if app.state.limiter or settings.daily_budget_usd is not None or settings.admin_token:
        log.info("public demo guards: rate limit %s/min %s/day, daily budget %s, admin token %s",
                 settings.rate_limit_per_minute or "-", settings.rate_limit_per_day or "-",
                 settings.daily_budget_usd, "set" if settings.admin_token else "unset")
    yield
    if app.state.alerts is not None:
        await app.state.alerts.stop()
    await app.state.agent.aclose()


app = FastAPI(title="TickerSage", version="0.1.0", lifespan=lifespan)
# The API is read-only and uses no cookies, so CORS only decides which frontends may call it.
# X-Admin-Token lets a frontend traces page send the admin token; X-Client-Id scopes alert rules to a browser;
# Retry-After is readable on a 429.
_web = get_settings()
app.add_middleware(CORSMiddleware, allow_origins=_web.cors_origins, allow_origin_regex=_web.cors_origin_regex,
                   allow_methods=["GET", "POST", "DELETE"],
                   allow_headers=["Content-Type", "X-Admin-Token", "X-Client-Id"],
                   expose_headers=["Retry-After"])


def _agent(request: Request) -> Agent:
    return request.app.state.agent


async def rate_limit(request: Request) -> None:
    limiter: RateLimiter | None = request.app.state.limiter
    if limiter is not None:
        await limiter.check(request)


async def require_admin(request: Request, x_admin_token: str | None = Header(default=None)) -> None:
    """Traces and the audit log hold user questions: with ADMIN_TOKEN set, only callers that send it may read them."""
    token = _agent(request).settings.admin_token
    if token and not (x_admin_token and secrets.compare_digest(x_admin_token.encode(), token.encode())):
        raise HTTPException(401, "admin token required: send it in the X-Admin-Token header")


@app.get("/api/health")
async def health(request: Request) -> dict:
    agent = _agent(request)
    s = agent.settings
    return {"status": "ok", "llm": agent.llm_mode, "tool_mode": s.tool_mode, "routing_mode": s.routing_mode,
            "planner_mode": s.planner_mode, "verifier": s.verifier_enabled}


@app.post("/api/chat", response_model=ChatResponse, dependencies=[Depends(rate_limit)])
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    return await _agent(request).ask(body.question, scopes=body.scopes)


@app.post("/api/chat/stream", dependencies=[Depends(rate_limit)])
async def chat_stream(body: ChatRequest, request: Request) -> EventSourceResponse:
    """SSE: `step` events while the graph runs, then the verified answer in `token` chunks, then `done`.

    The answer is streamed only after the number verifier and output guard have run,
    so the client never displays an unverified figure.
    """
    agent = _agent(request)

    async def events():
        async for event in agent.stream(body.question, scopes=body.scopes):
            if event["type"] == "step":
                yield {"event": "step", "data": json.dumps(event, default=str)}
                continue
            response: ChatResponse = event["response"]
            text = response.answer
            for i in range(0, len(text), ANSWER_CHUNK):
                yield {"event": "token", "data": json.dumps({"delta": text[i : i + ANSWER_CHUNK]})}
            yield {"event": "done", "data": response.model_dump_json()}

    return EventSourceResponse(events())


@app.get("/api/traces", dependencies=[Depends(require_admin)])
async def list_traces(request: Request, limit: int = 50) -> list[dict]:
    store = _agent(request).traces
    return [] if store is None else json.loads(json.dumps(store.recent(min(limit, 500)), default=str))


@app.get("/api/traces/{request_id}", dependencies=[Depends(require_admin)])
async def get_trace(request_id: str, request: Request) -> dict:
    store = _agent(request).traces
    row = store.get(request_id) if store else None
    if row is None:
        raise HTTPException(404, "trace not found")
    return json.loads(json.dumps(row, default=str))


@app.get("/api/refusals", dependencies=[Depends(require_admin)])
async def refusal_counts(request: Request) -> dict:
    store = _agent(request).traces
    return {} if store is None else store.refusal_counts()


@app.get("/api/audit", dependencies=[Depends(require_admin)])
async def audit(request: Request, limit: int = 100, request_id: str | None = None,
                decision: Literal["allow", "deny"] | None = None) -> list[dict]:
    """The policy engine's decision on every proposed tool call, newest first."""
    store = _agent(request).traces
    rows = [] if store is None else store.audit(min(limit, 1000), request_id, decision)
    return json.loads(json.dumps(rows, default=str))


@app.get("/api/evals/latest")
async def latest_evals() -> dict:
    if not EVAL_RESULTS.exists():
        raise HTTPException(404, "no eval results yet: run `python -m evals.run_evals`")
    return json.loads(EVAL_RESULTS.read_text())


@app.get("/evals", include_in_schema=False)
async def evals_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "evals.html")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.include_router(strategy_router)  # Strategy Studio: compile and backtest, separate from the chat graph
app.include_router(report_router)  # multi-agent research reports
app.include_router(alerts_router)  # watchlists and smart alerts: compile, store, check on a schedule, notify
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
