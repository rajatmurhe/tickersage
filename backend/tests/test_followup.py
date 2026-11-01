import pytest

from app.followup import resolve_followup
from app.symbols import SymbolRegistry

REG = SymbolRegistry()


@pytest.mark.parametrize(
    "history,question,expected",
    [
        (["What's BTC trading at?"], "and ETH?", "What's ETH trading at?"),
        (["What's the price of bitcoin?"], "what about solana", "What's the price of SOL?"),
        (["Compare ETH and SOL over the last 7 days"], "and BTC and DOGE?", "Compare BTC and DOGE over the last 7 days"),
        (["How volatile was ETH this week?"], "and over 30 days?", "How volatile was ETH this week, over 30 days?"),
        # A full question stands on its own.
        (["What's BTC trading at?"], "What is a funding rate?", "What is a funding rate?"),
        ([], "and ETH?", "and ETH?"),
        # Nothing to carry over from a question without an asset.
        (["What is a funding rate?"], "and ETH?", "and ETH?"),
    ],
)
def test_resolve_followup(history, question, expected):
    assert resolve_followup(question, history, REG) == expected


def test_rewritten_followup_still_goes_through_the_guardrails(offline_agent):
    q = resolve_followup("and SOL?", ["Should I buy BTC now?"], REG)
    assert q == "Should I buy SOL now?"


def test_api_answers_a_followup(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as main_module

    from .conftest import replay_settings

    settings = replay_settings(database_url=f"sqlite:///{tmp_path / 't.db'}")
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    with TestClient(main_module.app) as c:
        body = c.post("/api/chat", json={"question": "and ETH?", "history": ["What's BTC trading at?"]}).json()
    assert body["interpreted_as"] == "What's ETH trading at?"
    assert body["intent"] == "price" and body["trace"]["tools"][0]["args"]["symbol"] == "ETHUSDT"
