"""The policy engine: scopes, the tool allow-list, argument limits, the call budget and the audit log."""

import json
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main as main_module
from app.agent import Agent
from app.planner import plan_rules
from app.policy import Policy
from app.schemas import RouteDecision, Scope, ToolCall, ToolName
from app.tools.registry import INPUT_MODELS, TOOL_SCOPES

from .conftest import ScriptedProvider, llm_client, replay_settings, route_json

READ_SCOPES = {"market:read", "news:read", "docs:read"}


def test_every_tool_maps_to_a_read_scope():
    assert set(TOOL_SCOPES) == set(INPUT_MODELS) == set(get_args(ToolName))
    assert set(TOOL_SCOPES.values()) == set(get_args(Scope)) == READ_SCOPES
    assert Policy().scopes == READ_SCOPES == set(replay_settings().policy_scopes)


@pytest.mark.parametrize("route", [
    RouteDecision(intent="price", symbols=["BTC", "ETH", "SOL", "BNB"], wants_order_book=True),
    RouteDecision(intent="metrics", symbols=["BTC", "ETH", "SOL"], window="30d", wants_order_book=True),
    RouteDecision(intent="compare", symbols=["BTC", "ETH", "SOL", "BNB", "XRP", "ADA"], window="24h"),
    RouteDecision(intent="explain_move", symbols=["DOGE"]),
    RouteDecision(intent="concept", topic="funding rate"),
])
def test_default_policy_allows_every_rules_plan(route):
    decisions = Policy().review(plan_rules(route, "q").calls)
    assert decisions and all(d.allowed for d in decisions)


@pytest.mark.parametrize("tool, args, scopes, reason", [
    ("place_order", {"symbol": "BTCUSDT", "side": "BUY"}, None, "no such read-only tool"),
    ("get_news_sentiment", {"query": "bitcoin"}, ["market:read"], "needs the news:read scope"),
    ("search_docs", {"query": "funding rate"}, [], "needs the docs:read scope"),
    ("compare_assets", {"symbols": ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA"], "window": "7d"}, None,
     "asks for 6 symbols; the limit is 5"),
    ("compute_metrics", {"symbol": "BTC", "window": "1y"}, None, "window '1y' is not allowed (24h, 7d or 30d)"),
    ("get_klines", {"symbol": "BTC", "interval": "1s", "limit": 24}, None, "interval '1s' is not allowed"),
    ("get_klines", {"symbol": "BTC", "interval": "1h", "limit": 501}, None, "candle limit 501 is over the limit of 500"),
    ("get_order_book", {"symbol": "BTC", "depth": 1000}, None, "order-book depth 1000 is over the limit of 100"),
    ("get_order_book", {"symbol": "BTC", "depth": "max"}, None, "order-book depth must be a whole number"),
])
def test_denials_carry_a_reason(tool, args, scopes, reason):
    d = Policy().for_request(scopes).check(tool, args)
    assert d.decision == "deny" and reason in d.reason and d.tool == tool and d.args == args


def test_call_budget_allows_the_first_calls_and_denies_the_rest():
    calls = [ToolCall(tool="get_ticker", args={"symbol": s}) for s in ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOT"]]
    assert [d.decision for d in Policy().review(calls)] == ["allow"] * 6 + ["deny"]
    assert "over the limit of 6 tool calls" in Policy().review(calls)[-1].reason
    assert [d.decision for d in Policy().review(calls[:2], already_allowed=5)] == ["allow", "deny"]


def test_a_denied_call_does_not_use_up_the_budget():
    calls = [ToolCall(tool="get_news_sentiment", args={"query": "x"})] * 3 + [ToolCall(tool="get_ticker", args={})]
    decisions = Policy(max_calls=1).for_request(["market:read"]).review(calls)
    assert [d.decision for d in decisions] == ["deny", "deny", "deny", "allow"]


def test_request_scopes_can_narrow_but_never_widen():
    base = Policy(scopes=frozenset({"market:read", "docs:read"}))
    assert base.for_request(None) is base
    assert base.for_request(["market:read", "news:read"]).scopes == {"market:read"}
    assert base.for_request([]).scopes == frozenset()


def test_settings_shape_the_policy():
    policy = Policy.from_settings(replay_settings(policy_allowed_tools=["get_ticker", "get_order_book"],
                                                  policy_max_order_book_depth=20, policy_max_tool_calls=2))
    assert "allow-list for this deployment" in policy.check("compute_metrics", {"symbol": "BTC"}).reason
    assert policy.check("get_order_book", {"symbol": "BTC", "depth": 20}).allowed
    assert not policy.check("get_order_book", {"symbol": "BTC", "depth": 50}).allowed
    assert policy.max_calls == 2


def test_settings_reject_scopes_that_do_not_exist():
    with pytest.raises(ValidationError):
        replay_settings(policy_scopes=["market:read", "trade:write"])
    with pytest.raises(ValidationError):
        replay_settings(policy_allowed_tools=["place_order"])


# ------------------------------------------------------------- in the graph

async def _counting(agent: Agent) -> list[str]:
    """Records every tool call that reaches the toolbox."""
    ran: list[str] = []
    real = agent.deps.toolbox.call

    async def call(c, call_id, opts=None):
        ran.append(c.tool)
        return await real(c, call_id, opts)

    agent.deps.toolbox.call = call  # type: ignore[method-assign]
    return ran


async def test_denied_call_never_runs_and_the_answer_says_why(offline_agent):
    ran = await _counting(offline_agent)
    r = await offline_agent.ask("Why might BTC have dropped today?", scopes=["market:read"])
    assert ran == ["get_ticker", "compute_metrics"] and r.status == "ok"
    executor = [s for s in r.trace.steps if s.name == "executor"][0]
    assert executor.detail["policy"] == {
        "scopes": ["market:read"], "allowed": 2,
        "denied": [
            # attribute_move's evidence pack includes a news-sentiment shift, so it needs news:read too
            {"tool": "attribute_move", "args": {"symbol": "BTC", "window": "24h"},
             "reason": "also returns data that needs the news:read scope, which this request doesn't have"},
            {"tool": "get_news_sentiment", "args": {"query": "Bitcoin"},
             "reason": "needs the news:read scope, which this request doesn't have"},
        ],
    }
    assert "**BTC** is trading at" in r.answer
    assert "- news sentiment: needs the news:read scope" in r.answer
    assert r.answer.index("needs the news:read scope") < r.answer.index("_Data as of")


async def test_every_call_denied_degrades_to_a_safe_answer(offline_agent):
    ran = await _counting(offline_agent)
    r = await offline_agent.ask("Compare ETH and SOL over the last 7 days", scopes=[])
    assert ran == [] and r.status == "safe_fallback" and r.trace.tools == []
    assert "I couldn't find data to answer that." in r.answer and "needs the market:read scope" in r.answer


async def test_cached_concept_answer_is_not_served_without_docs_scope(offline_agent):
    await offline_agent.ask("What is a funding rate?")
    r = await offline_agent.ask("What is a funding rate?", scopes=["market:read"])
    assert not any(s.name == "semantic_cache" and s.detail.get("hit") for s in r.trace.steps)
    assert r.status == "safe_fallback" and "needs the docs:read scope" in r.answer
    again = await offline_agent.ask("What is a funding rate?")
    assert [s for s in again.trace.steps if s.name == "semantic_cache"][0].detail["hit"]


async def test_refusal_snapshot_goes_through_the_policy(offline_agent):
    r = await offline_agent.ask("Should I buy SOL now?", scopes=["news:read"])
    assert r.status == "refused" and r.trace.tools == [] and "**SOL** is trading at" not in r.answer
    refuse = [s for s in r.trace.steps if s.name == "refuse"][0]
    assert refuse.detail["policy"]["denied"][0]["tool"] == "get_ticker"


async def test_llm_planner_proposal_outside_the_scope_is_denied():
    plan = json.dumps({"calls": [
        {"tool": "get_ticker", "symbol": "BTC", "symbols": None, "window": None, "interval": None, "limit": None,
         "depth": None, "query": None},
        {"tool": "get_news_sentiment", "symbol": None, "symbols": None, "window": None, "interval": None,
         "limit": None, "depth": None, "query": "Bitcoin"},
    ]})
    claude = ScriptedProvider("anthropic", {"router": [route_json("price", ["BTC"])], "planner": [plan]},
                              default="BTC is listed on Binance [1].")
    s = replay_settings(llm_mode="live", planner_mode="llm")
    agent = await Agent.create(s, llm=llm_client(s, claude), store_traces=False)
    r = await agent.ask("What's BTC trading at?", scopes=["market:read"])
    await agent.aclose()
    planner = [st for st in r.trace.steps if st.name == "planner"][0]
    assert planner.detail["mode"] == "llm" and len(planner.detail["calls"]) == 2  # the model proposed both
    assert [t["tool"] for t in r.trace.tools] == ["get_ticker"]  # the policy let one run
    executor = [st for st in r.trace.steps if st.name == "executor"][0]
    assert [d["tool"] for d in executor.detail["policy"]["denied"]] == ["get_news_sentiment"]


# ------------------------------------------------------------- API and audit log

@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def make(**overrides) -> TestClient:
        settings = replay_settings(database_url=f"sqlite:///{tmp_path / 'traces.db'}", **overrides)
        monkeypatch.setattr(main_module, "get_settings", lambda: settings)
        return TestClient(main_module.app)
    return make


def test_chat_takes_scopes_and_rejects_unknown_ones(make_client):
    with make_client() as c:
        r = c.post("/api/chat", json={"question": "What is a funding rate?", "scopes": ["market:read"]})
        assert r.status_code == 200 and r.json()["status"] == "safe_fallback"
        assert c.post("/api/chat", json={"question": "BTC?", "scopes": ["trade:write"]}).status_code == 422
        assert c.post("/api/chat", json={"question": "What's ETH trading at?"}).json()["status"] == "ok"


def test_audit_log_records_every_decision(make_client):
    with make_client() as c:
        q = {"question": "Why might BTC have dropped today?", "scopes": ["market:read", "docs:read"]}
        rid = c.post("/api/chat", json=q).json()["request_id"]
        c.post("/api/chat", json={"question": "What's ETH trading at?"})
        rows = c.get("/api/audit", params={"request_id": rid}).json()
        assert sorted((r["tool"], r["decision"]) for r in rows) == [
            ("attribute_move", "deny"), ("compute_metrics", "allow"), ("get_news_sentiment", "deny"),
            ("get_ticker", "allow")]
        denied = [r for r in rows if r["tool"] == "get_news_sentiment"][0]
        assert denied["scopes"] == ["docs:read", "market:read"] and denied["required_scope"] == "news:read"
        assert denied["args"] == {"query": "Bitcoin"} and "news:read" in denied["reason"] and denied["created_at"]
        assert sorted(r["tool"] for r in c.get("/api/audit", params={"decision": "deny"}).json()) == [
            "attribute_move", "get_news_sentiment"]
        assert len(c.get("/api/audit").json()) == 5 and c.get("/api/audit?decision=maybe").status_code == 422


def test_audit_log_needs_the_admin_token_when_set(make_client):
    with make_client(admin_token="s3cret") as c:
        c.post("/api/chat", json={"question": "What's ETH trading at?"})
        assert c.get("/api/audit").status_code == 401
        assert c.get("/api/audit", headers={"X-Admin-Token": "wrong"}).status_code == 401
        rows = c.get("/api/audit", headers={"X-Admin-Token": "s3cret"}).json()
        assert [(r["tool"], r["decision"]) for r in rows] == [("get_ticker", "allow")]


def test_a_tool_needs_every_scope_whose_data_it_returns():
    from app.policy import Policy
    from app.tools.registry import TOOL_EXTRA_SCOPES

    assert TOOL_EXTRA_SCOPES == {"attribute_move": ("news:read",)}
    market_only = Policy().for_request(["market:read"])
    decision = market_only.check("attribute_move", {"symbol": "BTC", "window": "24h"})
    assert decision.decision == "deny" and "news:read" in decision.reason
    assert Policy().check("attribute_move", {"symbol": "BTC", "window": "24h"}).decision == "allow"
