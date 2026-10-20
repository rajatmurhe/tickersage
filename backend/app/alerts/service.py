"""The alert service: checks rules, decides whether they fire, writes the brief, stores and delivers the alert.

The scheduler is a plain asyncio loop started with the app: every ALERTS_CHECK_INTERVAL_S seconds it checks
each enabled rule (a few at a time) with the read-only tools, through the policy engine. A rule fires when its
conditions become true; it then waits until they are false again before it can fire again (so a level that
stays crossed alerts once), and never fires twice within its cooldown.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..tools.registry import RunOptions
from .brief import write_brief
from .evaluator import Action, check, decide
from .notify import deliver
from .spec import AlertSpec

if TYPE_CHECKING:
    from ..agent import Agent
    from .store import AlertStore

log = logging.getLogger("tickersage.alerts")


class AlertService:
    def __init__(self, agent: Agent, store: AlertStore) -> None:
        self.agent = agent
        self.store = store
        self.settings = agent.settings
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()  # one scheduler pass at a time
        self.last_run: dict[str, Any] | None = None

    # ------------------------------------------------------------ one rule

    async def check_rule(self, rule: dict[str, Any], now: datetime | None = None, *,
                         opts: RunOptions | None = None) -> dict[str, Any]:
        """Check one stored rule and act on it. Returns the action, the evaluation and the event if it fired."""
        now = now or datetime.now(UTC)
        spec = AlertSpec.model_validate(rule["spec"])
        deps = self.agent.deps
        rid = f"alert-{uuid.uuid4().hex[:16]}"
        checked = await check(spec, deps.toolbox, deps.policy, state=rule.get("state") or {},
                              traces=self.agent.traces, request_id=rid, opts=opts)
        ev = checked.evaluation
        action: Action
        action, armed = decide(ev.met, bool(rule.get("armed", True)), rule.get("last_fired_at"), now,
                               spec.cooldown_minutes)
        values: dict[str, Any] = {"last_checked_at": now, "last_action": action, "armed": armed,
                                  "last_error": "; ".join(ev.errors)[:1000] or None}
        if action != "error":
            values["state"] = ev.state
        event = None
        if action == "fire":
            llm = deps.llm
            if llm is not None and await self.agent._budget_guard() is not None:
                llm = None  # today's LLM budget is spent: the template brief, which needs no model
            brief = await write_brief(spec, rule["text"], checked, deps.toolbox, deps.policy, llm=llm,
                                      traces=self.agent.traces)
            event = await self.store.add_event({
                "rule_id": rule["id"], "owner": rule["owner"], "created_at": now, "symbol": spec.symbol,
                "rule_text": rule["text"], "conditions": [c.model_dump() for c in ev.conditions],
                "brief": brief.text, "brief_source": brief.source,
                "verification": brief.verification.model_dump() | {"fallback_reason": brief.fallback_reason,
                                                                   "removed": brief.removed,
                                                                   "window": brief.window,
                                                                   "drivers": brief.drivers},
                "deliveries": {"in_app": "delivered"}, "request_id": rid, "read": False,
            })
            values.update(last_fired_at=now, fire_count=int(rule.get("fire_count") or 0) + 1)
            await self.store.update_rule(rule["id"], **values)
            deliveries = await deliver(self.settings, self.agent.http, list(rule.get("channels") or []),
                                       f"TickerSage alert: {spec.symbol}", brief.text)
            await self.store.set_deliveries(event["id"], deliveries)
            event["deliveries"] = deliveries
        else:
            await self.store.update_rule(rule["id"], **values)
        return {"rule_id": rule["id"], "action": action, "evaluation": ev.model_dump(mode="json"), "event": event}

    # ------------------------------------------------------------ the scheduler

    async def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        """One pass over every enabled rule."""
        async with self._lock:
            started = time.perf_counter()
            rules = await self.store.rules(enabled_only=True)
            sem = asyncio.Semaphore(max(1, self.settings.alerts_concurrency))
            counts: dict[str, int] = {}

            async def one(rule: dict[str, Any]) -> None:
                async with sem:
                    try:
                        out = await self.check_rule(rule, now)
                        action = out["action"]
                    except Exception:
                        log.exception("alert rule %s failed", rule.get("id"))
                        action = "error"
                    counts[action] = counts.get(action, 0) + 1

            await asyncio.gather(*(one(r) for r in rules))
            self.last_run = {"at": (now or datetime.now(UTC)).isoformat(), "rules": len(rules), "actions": counts,
                             "seconds": round(time.perf_counter() - started, 2)}
            if counts.get("fire"):
                log.info("alert pass: %s", self.last_run)
            return self.last_run

    async def _loop(self) -> None:
        interval = max(5.0, self.settings.alerts_check_interval_s)
        while True:
            await asyncio.sleep(interval)
            try:
                await self.run_once()
            except Exception:
                log.exception("alert scheduler pass failed")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="alert-scheduler")
            log.info("alert scheduler on: every %.0fs", self.settings.alerts_check_interval_s)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()
