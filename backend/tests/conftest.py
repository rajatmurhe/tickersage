from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent import Agent
from app.config import REPO_DIR, Settings
from app.llm import LLMClient, ProviderError, Usage

FIXTURES = REPO_DIR / "evals" / "fixtures"


def replay_settings(**overrides) -> Settings:
    base = dict(llm_mode="offline", tool_mode="replay", fixtures_dir=FIXTURES, database_url="sqlite://",
                anthropic_api_key=None, openai_api_key=None, openrouter_api_key=None, redis_url=None)
    base.update(overrides)
    return Settings(**base)


class ScriptedProvider:
    """A fake LLM provider. `script` maps a call role to a list of responses (str or Exception)."""

    def __init__(self, name: str, script: dict[str, list] | None = None, default: str | Exception = "ok"):
        self.name = name
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.default = default
        self.calls: list[dict] = []

    async def generate(self, *, model, system, user, max_tokens, json_schema, effort):
        role = "router" if "classify" in system else "planner" if "plan read-only" in system else \
            "judge" if "grade answers" in system else "synth"
        self.calls.append({"role": role, "model": model, "user": user, "json_schema": json_schema, "effort": effort})
        queue = self.script.get(role)
        item = queue.pop(0) if queue else self.default
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(user)
        return item, Usage(input_tokens=1000, output_tokens=200), model


def route_json(intent: str, symbols: list[str] | None = None, window: str | None = None, **kw) -> str:
    return json.dumps({"intent": intent, "symbols": symbols or [], "window": window,
                       "wants_order_book": kw.get("wants_order_book", False), "topic": kw.get("topic")})


def llm_client(settings: Settings, *providers: ScriptedProvider) -> LLMClient:
    return LLMClient(settings, {p.name: p for p in providers})


@pytest.fixture
async def offline_agent():
    agent = await Agent.create(replay_settings(), store_traces=False)
    yield agent
    await agent.aclose()


__all__ = ["FIXTURES", "ProviderError", "ScriptedProvider", "llm_client", "replay_settings", "route_json", "Path"]
