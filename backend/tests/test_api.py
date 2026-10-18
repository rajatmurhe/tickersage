import json

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.agent import Agent
from app.tracing import TraceStore

from .conftest import replay_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = replay_settings(database_url=f"sqlite:///{tmp_path / 'traces.db'}")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    with TestClient(main_module.app) as c:
        yield c


def test_health(client):
    body = client.get("/api/health").json()
    assert body["llm"] == "offline" and body["tool_mode"] == "replay"


def test_chat_and_trace_storage(client):
    r = client.post("/api/chat", json={"question": "What's ETH trading at?"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["trace"]["faithfulness"] == 1.0
    stored = client.get(f"/api/traces/{body['request_id']}").json()
    assert stored["intent"] == "price" and stored["tools"][0]["tool"] == "get_ticker"
    client.post("/api/chat", json={"question": "Should I buy ETH?"})
    assert client.get("/api/refusals").json() == {"advice": 1}


def test_cors_allows_local_frontend_only(client):
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    ok = client.options("/api/chat/stream", headers={"Origin": "http://localhost:3077", **preflight})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:3077"
    other = client.options("/api/chat/stream", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in other.headers


def test_chat_rejects_empty_question(client):
    assert client.post("/api/chat", json={"question": ""}).status_code == 422


def test_stream_emits_steps_then_verified_answer(client):
    with client.stream("POST", "/api/chat/stream", json={"question": "Compare ETH and SOL over 7 days"}) as r:
        raw = "".join(r.iter_text())
    events = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    kinds = [e for e, _ in events]
    assert kinds[0] == "step" and kinds[-1] == "done" and "token" in kinds
    # the verifier step finishes before the first answer token is sent
    assert kinds.index("token") > max(i for i, (e, d) in enumerate(events) if e == "step" and d["node"] == "verifier")
    streamed = "".join(d["delta"] for e, d in events if e == "token")
    assert streamed == events[-1][1]["answer"]


def test_static_pages(client):
    assert "TickerSage" in client.get("/").text
    assert "eval results" in client.get("/evals").text


async def test_trace_store_roundtrip(tmp_path):
    agent = await Agent.create(replay_settings(), store_traces=False)
    r = await agent.ask("What's BTC trading at?")
    await agent.aclose()
    store = TraceStore(f"sqlite:///{tmp_path / 't.db'}")
    await store.save("What's BTC trading at?", r.trace)
    row = store.get(r.request_id)
    assert row["status"] == "ok" and row["faithfulness"] == 1.0 and row["steps"][0]["name"] == "input_guard"
