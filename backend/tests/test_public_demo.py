"""Public-demo guards: per-IP rate limits, the daily LLM budget and the admin token on traces."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import insert
from starlette.requests import Request

from app import main as main_module
from app.agent import Agent
from app.ratelimit import DAY, Limit, MemoryCounter, RateLimiter
from app.tracing import TraceStore, traces

from .conftest import ScriptedProvider, llm_client, replay_settings, route_json

NOON = 1_800_000_000 - 1_800_000_000 % DAY + DAY // 2  # 12:00:00 UTC on some day


class Clock:
    def __init__(self, now: float = NOON) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def make(**overrides) -> TestClient:
        settings = replay_settings(database_url=f"sqlite:///{tmp_path / 'traces.db'}", **overrides)
        monkeypatch.setattr(main_module, "get_settings", lambda: settings)
        return TestClient(main_module.app)
    return make


def _request(peer: str = "10.0.0.9", forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "method": "POST", "path": "/api/chat", "headers": headers, "client": (peer, 5000)})


def _freeze(client: TestClient) -> Clock:
    """Pins the limiter's clock, so a test can't straddle a minute boundary."""
    clock = Clock()
    client.app.state.limiter.counter = MemoryCounter(clock)
    return clock


# ------------------------------------------------------------- rate limiting

def test_rate_limit_is_off_by_default():
    assert RateLimiter.from_settings(replay_settings()) is None


def test_chat_returns_429_with_json_message_after_minute_limit(make_client):
    q = {"question": "What's ETH trading at?"}
    with make_client(rate_limit_per_minute=2) as c:
        _freeze(c)
        assert [c.post("/api/chat", json=q).status_code for _ in range(2)] == [200, 200]
        r = c.post("/api/chat", json=q)
        assert r.status_code == 429 and r.headers["content-type"] == "application/json"
        assert "2 questions per minute" in r.json()["detail"] and r.headers["retry-after"] == "60"
        # the streaming endpoint shares the same per-IP count
        assert c.post("/api/chat/stream", json=q).status_code == 429
        assert c.get("/api/health").status_code == 200  # only the chat endpoints are limited


async def test_minute_and_day_windows_reset():
    clock = Clock()
    limiter = RateLimiter([Limit("minute", 2, 60), Limit("day", 3, DAY)], MemoryCounter(clock))
    await limiter.check(_request())
    await limiter.check(_request())
    with pytest.raises(HTTPException) as minute:
        await limiter.check(_request())
    assert minute.value.status_code == 429 and "per minute" in minute.value.detail
    clock.now += 60
    await limiter.check(_request())  # third question today: the refused one didn't count against the day
    clock.now += 60
    with pytest.raises(HTTPException) as day:
        await limiter.check(_request())
    assert "3 questions per day" in day.value.detail and "Try again in 12 h" in day.value.detail
    assert day.value.headers["Retry-After"] == str(DAY // 2 - 120)
    await limiter.check(_request("10.0.0.10"))  # another visitor has their own count
    clock.now += DAY // 2  # past midnight UTC
    await limiter.check(_request())


def test_forwarded_for_is_ignored_unless_proxy_is_trusted():
    untrusted = RateLimiter([Limit("minute", 1, 60)], MemoryCounter())
    assert untrusted.client_ip(_request("10.0.0.9", "1.2.3.4")) == "10.0.0.9"
    trusted = RateLimiter([Limit("minute", 1, 60)], MemoryCounter(), trust_proxy=True)
    # the proxy appends the address it saw; whatever the client sent sits to its left and is ignored
    assert trusted.client_ip(_request("10.0.0.9", "6.6.6.6, 1.2.3.4")) == "1.2.3.4"
    assert trusted.client_ip(_request("10.0.0.9")) == "10.0.0.9"
    two_hops = RateLimiter([Limit("minute", 1, 60)], MemoryCounter(), trust_proxy=True, proxy_hops=2)
    assert two_hops.client_ip(_request("10.0.0.9", "6.6.6.6, 1.2.3.4, 172.16.0.1")) == "1.2.3.4"


def test_spoofed_forwarded_for_does_not_reset_the_limit(make_client):
    q = {"question": "What's ETH trading at?"}
    with make_client(rate_limit_per_minute=1, trust_proxy=True) as c:
        _freeze(c)
        assert c.post("/api/chat", json=q, headers={"X-Forwarded-For": "1.1.1.1, 9.9.9.9"}).status_code == 200
        assert c.post("/api/chat", json=q, headers={"X-Forwarded-For": "2.2.2.2, 9.9.9.9"}).status_code == 429
        assert c.post("/api/chat", json=q, headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200


# ------------------------------------------------------------- daily budget

def _price_provider() -> ScriptedProvider:
    return ScriptedProvider("anthropic", {"router": [route_json("price", ["BTC"])] * 5},
                            default="BTC is listed on Binance [1].")


async def _budget_agent(tmp_path, provider: ScriptedProvider, budget: float) -> Agent:
    s = replay_settings(llm_mode="live", database_url=f"sqlite:///{tmp_path / 'traces.db'}", daily_budget_usd=budget)
    return await Agent.create(s, llm=llm_client(s, provider))


async def test_daily_budget_switches_to_offline_answers(tmp_path):
    claude = _price_provider()
    agent = await _budget_agent(tmp_path, claude, budget=0.0003)
    live = await agent.ask("What's BTC trading at?")
    assert live.trace.cost_usd > 0.0003 and not live.trace.fell_back  # router + synth on Haiku: $0.0004
    calls = len(claude.calls)

    events = [e async for e in agent.stream("What's BTC trading at?")]
    capped = events[-1]["response"]
    await agent.aclose()
    assert len(claude.calls) == calls  # no LLM call once the budget is spent
    assert events[0]["node"] == "budget_guard" and events[0]["detail"]["detail"]["llm"] == "offline"
    assert capped.status == "ok" and "**BTC** is trading at" in capped.answer  # the offline template answer
    assert capped.trace.fell_back and capped.trace.cost_usd == 0 and capped.trace.models == []
    guard = capped.trace.steps[0]
    assert guard.name == "budget_guard" and guard.detail["spent_today_usd"] == pytest.approx(0.0004)
    stored = agent.traces.get(capped.request_id)
    assert stored["fell_back"] and stored["steps"][0]["name"] == "budget_guard"


async def test_daily_budget_counts_only_todays_spend(tmp_path):
    claude = _price_provider()
    agent = await _budget_agent(tmp_path, claude, budget=1.0)
    yesterday = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
    with agent.traces.engine.begin() as conn:
        conn.execute(insert(traces).values(request_id="old", created_at=yesterday, question="q", status="ok",
                                           cost_usd=5.0))
    assert agent.traces.cost_since(yesterday) == 5.0 and await agent.traces.cost_today() == 0.0
    r = await agent.ask("What's BTC trading at?")
    await agent.aclose()
    assert claude.calls and not r.trace.fell_back and r.trace.steps[0].name == "input_guard"


def test_blank_budget_means_no_cap():
    assert replay_settings(daily_budget_usd="").daily_budget_usd is None
    assert replay_settings(daily_budget_usd="2.5").daily_budget_usd == 2.5


async def test_trace_store_sums_cost_since(tmp_path):
    store = TraceStore(f"sqlite:///{tmp_path / 't.db'}")
    now = datetime.now(UTC)
    with store.engine.begin() as conn:
        for i, (age_h, cost) in enumerate([(0, 0.25), (1, 0.5), (30, 9.0)]):
            conn.execute(insert(traces).values(request_id=str(i), created_at=now - timedelta(hours=age_h),
                                               question="q", status="ok", cost_usd=cost))
    assert store.cost_since(now - timedelta(hours=2)) == 0.75
    assert store.cost_since(now + timedelta(hours=1)) == 0.0


# ------------------------------------------------------------- admin token

def test_traces_require_admin_token_when_set(make_client):
    with make_client(admin_token="s3cret") as c:
        rid = c.post("/api/chat", json={"question": "What's ETH trading at?"}).json()["request_id"]
        for path in ("/api/traces", f"/api/traces/{rid}", "/api/refusals"):
            assert c.get(path).status_code == 401
            r = c.get(path, headers={"X-Admin-Token": "wrong"})
            assert r.status_code == 401 and "X-Admin-Token" in r.json()["detail"]
            assert c.get(path, headers={"X-Admin-Token": "s3cret"}).status_code == 200
        assert c.get("/api/traces", headers={"X-Admin-Token": "s3cret"}).json()[0]["request_id"] == rid
        assert c.get("/api/health").status_code == 200 and c.get("/api/evals/latest").status_code in (200, 404)


def test_traces_stay_open_without_admin_token(make_client):
    with make_client() as c:
        assert c.get("/api/traces").status_code == 200 and c.get("/api/refusals").status_code == 200


def test_cors_lets_a_frontend_send_the_admin_token(make_client):
    preflight = {"Origin": "http://localhost:3077", "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "x-admin-token"}
    with make_client(admin_token="s3cret") as c:
        r = c.options("/api/traces", headers=preflight)
    assert r.status_code == 200 and "x-admin-token" in r.headers["access-control-allow-headers"].lower()
