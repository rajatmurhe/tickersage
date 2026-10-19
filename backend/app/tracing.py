"""Request traces and the tool-call audit log, in SQL tables: PostgreSQL in production, SQLite by default.

traces: one row per request: intent, tools called, tokens in and out, cost, per-step latency,
models used, whether a provider fallback happened, final status and refusal reason.

audit_log: one row per tool call the planner proposed, with the policy engine's decision
(allow or deny) and its reason, the request's scopes and the call's arguments.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text, create_engine, func, insert, select,
)

from .schemas import Trace

log = logging.getLogger(__name__)

metadata = MetaData()

traces = Table(
    "traces",
    metadata,
    Column("request_id", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("question", Text, nullable=False),
    Column("intent", String(32)),
    Column("status", String(32), nullable=False),
    Column("refusal_reason", Text),
    Column("tools", JSON),
    Column("steps", JSON),
    Column("models", JSON),
    Column("input_tokens", Integer, default=0),
    Column("output_tokens", Integer, default=0),
    Column("cost_usd", Float, default=0.0),
    Column("latency_ms", Float, default=0.0),
    Column("fell_back", Boolean, default=False),
    Column("faithfulness", Float),
    Column("unsupported_numbers", JSON),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("request_id", String(64), nullable=False, index=True),
    Column("scopes", JSON),  # what the request was allowed to touch
    Column("tool", String(64), nullable=False),
    Column("required_scope", String(32)),
    Column("args", JSON),
    Column("decision", String(8), nullable=False),  # allow | deny
    Column("reason", Text, nullable=False),
)


class TraceStore:
    def __init__(self, database_url: str) -> None:
        url = database_url
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        self.engine = create_engine(url, pool_pre_ping=True, future=True)
        metadata.create_all(self.engine)

    def _save(self, question: str, trace: Trace) -> None:
        row: dict[str, Any] = {
            "request_id": trace.request_id,
            "created_at": datetime.now(UTC),
            "question": question,
            "intent": trace.intent,
            "status": trace.status,
            "refusal_reason": trace.refusal_reason,
            "tools": trace.tools,
            "steps": [s.model_dump() for s in trace.steps],
            "models": trace.models,
            "input_tokens": trace.input_tokens,
            "output_tokens": trace.output_tokens,
            "cost_usd": trace.cost_usd,
            "latency_ms": trace.latency_ms,
            "fell_back": trace.fell_back,
            "faithfulness": trace.faithfulness,
            "unsupported_numbers": trace.unsupported_numbers,
        }
        with self.engine.begin() as conn:
            conn.execute(insert(traces).values(**json.loads(json.dumps(row, default=str)) | {"created_at": row["created_at"]}))

    async def save(self, question: str, trace: Trace) -> None:
        try:
            await asyncio.to_thread(self._save, question, trace)
        except Exception:
            log.exception("failed to store trace %s", trace.request_id)

    def _save_audit(self, request_id: str, scopes: list[str], decisions: list[dict[str, Any]]) -> None:
        now = datetime.now(UTC)
        rows = [{"created_at": now, "request_id": request_id, "scopes": scopes, "tool": d["tool"],
                 "required_scope": d["scope"], "args": json.loads(json.dumps(d["args"], default=str)),
                 "decision": d["decision"], "reason": d["reason"]} for d in decisions]
        with self.engine.begin() as conn:
            conn.execute(insert(audit_log), rows)

    async def save_audit(self, request_id: str, scopes: list[str], decisions: list[dict[str, Any]]) -> None:
        if not decisions:
            return
        try:
            await asyncio.to_thread(self._save_audit, request_id, scopes, decisions)
        except Exception:
            log.exception("failed to store audit rows for %s", request_id)

    def audit(self, limit: int = 100, request_id: str | None = None, decision: str | None = None) -> list[dict[str, Any]]:
        """Newest first, optionally for one request or one decision."""
        query = select(audit_log).order_by(audit_log.c.id.desc()).limit(limit)
        if request_id:
            query = query.where(audit_log.c.request_id == request_id)
        if decision:
            query = query.where(audit_log.c.decision == decision)
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(query).mappings().all()]

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(traces).order_by(traces.c.created_at.desc()).limit(limit)).mappings().all()
        return [dict(r) for r in rows]

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(traces).where(traces.c.request_id == request_id)).mappings().first()
        return dict(row) if row else None

    def cost_since(self, since: datetime) -> float:
        """Total recorded LLM cost of the requests stored since `since`."""
        query = select(func.coalesce(func.sum(traces.c.cost_usd), 0.0)).where(traces.c.created_at >= since)
        with self.engine.connect() as conn:
            return float(conn.execute(query).scalar_one())

    async def cost_today(self) -> float:
        """Total recorded LLM cost since midnight UTC."""
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        return await asyncio.to_thread(self.cost_since, midnight)

    def refusal_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.recent(limit=10_000):
            if row["status"] == "refused":
                reason = (row["refusal_reason"] or "unknown").split(":")[0]
                counts[reason] = counts.get(reason, 0) + 1
        return counts
