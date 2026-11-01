"""Per-user Telegram: a one-time link connects a chat to one browser's client id, and alerts go only there."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main as main_module

from .conftest import replay_settings

ALICE = {"X-Client-Id": "alice-browser-01"}
BOB = {"X-Client-Id": "bob-browser-0002"}
SEND = "https://api.telegram.org/bot123:abc/sendMessage"


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = replay_settings(database_url=f"sqlite:///{tmp_path / 't.db'}", telegram_bot_token="123:abc",
                               telegram_bot_username="TickerSageBot", telegram_webhook_secret="s3cret",
                               alerts_scheduler=False, admin_token="adm")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    with TestClient(main_module.app) as c:
        yield c


def start(client, code: str, chat: int):
    return client.post("/api/telegram/webhook/s3cret", json={"message": {"chat": {"id": chat}, "text": f"/start {code}"}})


def test_link_connects_only_the_owner(client):
    with respx.mock(assert_all_called=False) as mock:
        sent = mock.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        link = client.post("/api/alerts/telegram/link", headers=ALICE).json()
        assert link["url"].startswith("https://t.me/TickerSageBot?start=")
        assert "alice" not in link["url"]  # the client id never leaves the browser
        code = link["url"].split("start=")[1]

        assert start(client, code, 111).status_code == 200
        assert json.loads(sent.calls[-1].request.content)["text"].startswith("Connected.")
        assert client.get("/api/alerts/status", headers=ALICE).json()["telegram_connected"] is True
        assert client.get("/api/alerts/status", headers=BOB).json()["telegram_connected"] is False

        # The code is one-use.
        start(client, code, 222)
        assert "expired" in json.loads(sent.calls[-1].request.content)["text"]

        client.post("/api/telegram/webhook/s3cret", json={"message": {"chat": {"id": 111}, "text": "/stop"}})
        assert client.get("/api/alerts/status", headers=ALICE).json()["telegram_connected"] is False


def test_webhook_rejects_the_wrong_secret(client):
    assert client.post("/api/telegram/webhook/guess", json={}).status_code == 404


def test_alert_run_needs_the_admin_token(client):
    assert client.post("/api/alerts/run").status_code == 401
    out = client.post("/api/alerts/run", headers={"X-Admin-Token": "adm"}).json()
    assert out["rules"] == 0


async def test_fired_alert_goes_to_the_owners_chat(tmp_path):
    from app.agent import Agent
    from app.alerts.service import AlertService
    from app.alerts.store import AlertStore

    s = replay_settings(telegram_bot_token="123:abc")
    agent = await Agent.create(s, store_traces=False)
    store = AlertStore(f"sqlite:///{tmp_path / 'a.db'}")
    service = AlertService(agent, store)
    code = await store.new_link_code("alice-browser-01")
    assert await store.claim_link_code(code, "111") == "alice-browser-01"
    spec = {"symbol": "BTC", "conditions": [{"kind": "price_level", "op": "above", "price": 1}],
            "cooldown_minutes": 60}
    from app.alerts.spec import AlertSpec

    spec = AlertSpec.model_validate(spec).model_dump(mode="json")
    rule = await store.add_rule("alice-browser-01", "BTC above $1", spec, [], ["in_app", "telegram"], "rules")
    with respx.mock(assert_all_called=False) as mock:
        sent = mock.post(SEND).mock(return_value=httpx.Response(200, json={"ok": True}))
        out = await service.check_rule(rule)
    await agent.aclose()
    assert out["action"] == "fire"
    assert out["event"]["deliveries"]["telegram"] == "delivered"
    assert json.loads(sent.calls[0].request.content)["chat_id"] == "111"
