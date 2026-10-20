"""Research report endpoints.

  POST /api/report        SSE: a `step` event as each node finishes (one per asset sub-agent), then `done` with the
                          report (Markdown, structured sections, chart data, citations, trace and the HTML export)
  POST /api/report/html   the same report as a standalone HTML file (print it to PDF from the browser)

Reports go through the same guardrails, policy engine, number verifier, rate limit and daily LLM budget as chat.
"""

from __future__ import annotations

import json
import re
from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

from .models import Report, ReportRequest
from .service import ReportService

router = APIRouter(prefix="/api/report", tags=["report"])


async def rate_limit(request: Request) -> None:
    """The same per-IP limiter as /api/chat."""
    limiter = getattr(request.app.state, "limiter", None)
    if limiter is not None:
        await limiter.check(request)


def _service(request: Request) -> ReportService:
    state = request.app.state
    if getattr(state, "reports", None) is None:
        state.reports = ReportService(state.agent)
    return state.reports


@router.post("", dependencies=[Depends(rate_limit)])
async def report_stream(body: ReportRequest, request: Request) -> EventSourceResponse:
    service = _service(request)

    async def events():
        async for event in service.stream(body.question, scopes=body.scopes):
            if event["type"] == "step":
                yield {"event": "step", "data": json.dumps(event, default=str)}
            else:
                report: Report = event["report"]
                yield {"event": "done", "data": report.model_dump_json()}

    return EventSourceResponse(events())


@router.post("/html", response_class=HTMLResponse, dependencies=[Depends(rate_limit)])
async def report_html(body: ReportRequest, request: Request) -> HTMLResponse:
    report = await _service(request).run(body.question, scopes=body.scopes)
    if report.html is None:  # refused, out of scope or needs a clarification: there is no report to export
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><p>{escape(report.message or '')}</p>",
                            status_code=422)
    name = re.sub(r"[^a-z0-9]+", "-", report.title.lower()).strip("-") or "report"
    return HTMLResponse(report.html, headers={"Content-Disposition": f'attachment; filename="{name}.html"'})

