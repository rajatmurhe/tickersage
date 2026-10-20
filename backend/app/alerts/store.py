"""Alert rules, fired alerts and the watchlist, in the same SQL database as the traces (SQLite by default,
PostgreSQL in production).

alert_rules: one row per rule: the text the user wrote, the validated spec, delivery channels, whether it is
enabled, and the scheduler's state (armed or waiting to re-arm, when it last fired, the last check's outcome).
alert_events: one row per fired alert: what triggered it, the brief, how each channel delivered it, read or not.
watchlist: the assets a user follows.

Rows carry an `owner`: the browser's client id, so visitors to a public demo see only their own rules.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine, delete,
    func, insert, select, update,
)
from sqlalchemy.pool import StaticPool

metadata = MetaData()

alert_rules = Table(
    "alert_rules",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("owner", String(64), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("text", Text, nullable=False),
    Column("spec", JSON, nullable=False),
    Column("summary", JSON),
    Column("channels", JSON),
    Column("source", String(16)),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("armed", Boolean, nullable=False, default=True),
    Column("state", JSON),
    Column("last_checked_at", DateTime(timezone=True)),
    Column("last_fired_at", DateTime(timezone=True)),
    Column("last_action", String(16)),
    Column("last_error", Text),
    Column("fire_count", Integer, nullable=False, default=0),
)

alert_events = Table(
    "alert_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("rule_id", String(32), nullable=False, index=True),
    Column("owner", String(64), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("symbol", String(20), nullable=False),
    Column("rule_text", Text),
    Column("conditions", JSON),
    Column("brief", Text, nullable=False),
    Column("brief_source", String(16)),
    Column("verification", JSON),
    Column("deliveries", JSON),
    Column("request_id", String(64)),
    Column("read", Boolean, nullable=False, default=False),
)

watchlist = Table(
    "watchlist",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("owner", String(64), nullable=False, index=True),
    Column("symbol", String(20), nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("owner", "symbol", name="uq_watchlist_owner_symbol"),
)


def _utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes: they were stored as UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _row(r: Any) -> dict[str, Any]:
    d = dict(r._mapping)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = _utc(v)
    return d


class AlertStore:
    def __init__(self, database_url: str) -> None:
        url = database_url
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
        if url in ("sqlite://", "sqlite:///:memory:"):
            # One shared connection, or every worker thread would see its own empty in-memory database.
            kwargs.update(poolclass=StaticPool, connect_args={"check_same_thread": False})
        self.engine = create_engine(url, **kwargs)
        metadata.create_all(self.engine)

    async def _run(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    # ------------------------------------------------------------ rules

    def _add_rule(self, owner: str, text: str, spec: dict, summary: list[str], channels: list[str],
                  source: str) -> dict[str, Any]:
        row = {"id": uuid.uuid4().hex[:12], "owner": owner, "created_at": datetime.now(UTC), "text": text,
               "spec": spec, "summary": summary, "channels": channels, "source": source, "enabled": True,
               "armed": True, "state": {}, "fire_count": 0}
        with self.engine.begin() as conn:
            conn.execute(insert(alert_rules).values(**row))
        return row

    async def add_rule(self, owner: str, text: str, spec: dict, summary: list[str], channels: list[str],
                       source: str) -> dict[str, Any]:
        return await self._run(self._add_rule, owner, text, spec, summary, channels, source)

    def _rules(self, owner: str | None, enabled_only: bool) -> list[dict[str, Any]]:
        q = select(alert_rules).order_by(alert_rules.c.created_at.desc())
        if owner is not None:
            q = q.where(alert_rules.c.owner == owner)
        if enabled_only:
            q = q.where(alert_rules.c.enabled.is_(True))
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(q)]

    async def rules(self, owner: str | None = None, enabled_only: bool = False) -> list[dict[str, Any]]:
        return await self._run(self._rules, owner, enabled_only)

    def _rule(self, rule_id: str, owner: str | None) -> dict[str, Any] | None:
        q = select(alert_rules).where(alert_rules.c.id == rule_id)
        if owner is not None:
            q = q.where(alert_rules.c.owner == owner)
        with self.engine.connect() as conn:
            r = conn.execute(q).first()
        return _row(r) if r else None

    async def rule(self, rule_id: str, owner: str | None = None) -> dict[str, Any] | None:
        return await self._run(self._rule, rule_id, owner)

    def _count_rules(self, owner: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(alert_rules)
                                .where(alert_rules.c.owner == owner)).scalar_one()

    async def count_rules(self, owner: str) -> int:
        return await self._run(self._count_rules, owner)

    def _update_rule(self, rule_id: str, values: dict[str, Any]) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(alert_rules).where(alert_rules.c.id == rule_id).values(**values))

    async def update_rule(self, rule_id: str, **values: Any) -> None:
        await self._run(self._update_rule, rule_id, values)

    def _delete_rule(self, rule_id: str, owner: str) -> bool:
        with self.engine.begin() as conn:
            n = conn.execute(delete(alert_rules).where(alert_rules.c.id == rule_id, alert_rules.c.owner == owner))
            return n.rowcount > 0

    async def delete_rule(self, rule_id: str, owner: str) -> bool:
        return await self._run(self._delete_rule, rule_id, owner)

    # ------------------------------------------------------------ fired alerts

    def _add_event(self, row: dict[str, Any]) -> dict[str, Any]:
        row = json.loads(json.dumps(row, default=str)) | {"created_at": row["created_at"]}
        with self.engine.begin() as conn:
            row["id"] = conn.execute(insert(alert_events).values(**row)).inserted_primary_key[0]
        return row

    async def add_event(self, row: dict[str, Any]) -> dict[str, Any]:
        return await self._run(self._add_event, row)

    def _set_deliveries(self, event_id: int, deliveries: dict[str, str]) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(alert_events).where(alert_events.c.id == event_id).values(deliveries=deliveries))

    async def set_deliveries(self, event_id: int, deliveries: dict[str, str]) -> None:
        await self._run(self._set_deliveries, event_id, deliveries)

    def _events(self, owner: str, limit: int, unread_only: bool, rule_id: str | None) -> list[dict[str, Any]]:
        q = select(alert_events).where(alert_events.c.owner == owner).order_by(alert_events.c.id.desc()).limit(limit)
        if unread_only:
            q = q.where(alert_events.c.read.is_(False))
        if rule_id:
            q = q.where(alert_events.c.rule_id == rule_id)
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(q)]

    async def events(self, owner: str, limit: int = 50, unread_only: bool = False,
                     rule_id: str | None = None) -> list[dict[str, Any]]:
        return await self._run(self._events, owner, limit, unread_only, rule_id)

    def _unread(self, owner: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(alert_events).where(
                alert_events.c.owner == owner, alert_events.c.read.is_(False))).scalar_one()

    async def unread(self, owner: str) -> int:
        return await self._run(self._unread, owner)

    def _mark_read(self, owner: str, ids: list[int] | None) -> int:
        q = update(alert_events).where(alert_events.c.owner == owner, alert_events.c.read.is_(False))
        if ids is not None:
            q = q.where(alert_events.c.id.in_(ids))
        with self.engine.begin() as conn:
            return conn.execute(q.values(read=True)).rowcount

    async def mark_read(self, owner: str, ids: list[int] | None = None) -> int:
        return await self._run(self._mark_read, owner, ids)

    # ------------------------------------------------------------ watchlist

    def _watchlist(self, owner: str) -> list[dict[str, Any]]:
        q = select(watchlist.c.symbol, watchlist.c.added_at).where(watchlist.c.owner == owner) \
            .order_by(watchlist.c.added_at)
        with self.engine.connect() as conn:
            return [_row(r) for r in conn.execute(q)]

    async def watchlist(self, owner: str) -> list[dict[str, Any]]:
        return await self._run(self._watchlist, owner)

    def _watch(self, owner: str, symbol: str) -> bool:
        with self.engine.begin() as conn:
            exists = conn.execute(select(watchlist.c.id).where(watchlist.c.owner == owner,
                                                               watchlist.c.symbol == symbol)).first()
            if exists:
                return False
            conn.execute(insert(watchlist).values(owner=owner, symbol=symbol, added_at=datetime.now(UTC)))
            return True

    async def watch(self, owner: str, symbol: str) -> bool:
        return await self._run(self._watch, owner, symbol)

    def _unwatch(self, owner: str, symbol: str) -> bool:
        with self.engine.begin() as conn:
            return conn.execute(delete(watchlist).where(watchlist.c.owner == owner,
                                                        watchlist.c.symbol == symbol)).rowcount > 0

    async def unwatch(self, owner: str, symbol: str) -> bool:
        return await self._run(self._unwatch, owner, symbol)
