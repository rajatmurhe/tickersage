"""Secret redaction: what it catches, what it leaves alone, and proof the raw secret goes nowhere.

Every secret here is generated at test time (random characters, random BIP39 words, PEM bodies that
decode to "not a real key"), so nothing key-shaped is committed for scanners to flag.
"""

import base64
import hashlib
import json
import logging
import random
import string
import textwrap

import pytest
import yaml
from sqlalchemy import select

from app.agent import Agent
from app.config import REPO_DIR
from app.secrets_guard import BIP39_WORDS, WORDLIST, redact_secrets, secret_warning
from app.tools.registry import LOCAL_TOOLS
from app.tracing import audit_log, traces

from .conftest import ScriptedProvider, llm_client, replay_settings, route_json

BIP39_ENGLISH_SHA256 = "2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda"
rng = random.Random(1729)
WORDS = sorted(BIP39_WORDS)


def api_key() -> str:
    return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(64))


def hex_key() -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(64))


def seed(n: int = 12) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n))


def pem(kind: str = "", end: bool = True) -> str:
    body = textwrap.wrap(base64.b64encode(f"not a real key, {kind or 'pkcs8'} test {rng.random()}".encode() * 3)
                         .decode(), 64)
    lines = [f"-----BEGIN {kind}PRIVATE KEY-----", *body] + ([f"-----END {kind}PRIVATE KEY-----"] if end else [])
    return "\n".join(lines)


def test_bundled_wordlist_is_the_official_bip39_english_list():
    assert hashlib.sha256(WORDLIST.read_bytes()).hexdigest() == BIP39_ENGLISH_SHA256
    assert len(BIP39_WORDS) == 2048


@pytest.mark.parametrize("make, kind", [
    (lambda: f"BTC price? key {api_key()} secret {api_key()}", "api_key"),
    (lambda: f"apiKey={api_key()}&timestamp=1", "api_key"),
    (lambda: f"what's SOL at? 0x{hex_key()}", "private_key"),
    (lambda: f"what's SOL at? {hex_key().upper()}", "private_key"),
    (lambda: f"my seed: {seed()}. is it safe?", "seed_phrase"),
    (lambda: "words: " + " ".join(f"{i}. {w}" for i, w in enumerate(seed(24).split(), 1)), "seed_phrase"),
    (lambda: ", ".join(w.capitalize() for w in seed(15).split()), "seed_phrase"),
    (lambda: f"{seed(6)} zzyzx {seed(5)}", "seed_phrase"),  # one mistyped word
    (lambda: f"what is a funding rate?\n{pem()}", "pem_private_key"),
    (lambda: f"{pem('RSA ', end=False)}\nwhat's BNB at?", "pem_private_key"),
    (lambda: pem("OPENSSH "), "pem_private_key"),
    (lambda: pem("EC "), "pem_private_key"),
    (lambda: " ".join(pem("ENCRYPTED ").split("\n")), "pem_private_key"),  # pasted on one line
])
def test_detects_and_redacts(make, kind):
    text = make()
    r = redact_secrets(text)
    assert r.kinds == [kind] and r.found and "[REDACTED_" in r.text
    assert not redact_secrets(r.text).found  # nothing left to find


def test_keeps_the_question_around_the_secret():
    r = redact_secrets(f"ETH price please. My backup words: {seed(24)}")
    assert r.text == "ETH price please. My backup words: [REDACTED_SEED_PHRASE]"
    r = redact_secrets(f"{pem('RSA ', end=False)}\nwhat's BNB at?")
    assert r.text == "[REDACTED_PEM_PRIVATE_KEY]\nwhat's BNB at?"
    multi = redact_secrets(f"LTC? {api_key()} {seed()} 0x{hex_key()}")
    assert multi.count == 3 and multi.kinds == ["private_key", "api_key", "seed_phrase"]


@pytest.mark.parametrize("text", [
    "What's ETH at? My address is 0x52908400098527886E0F7030069857D2E4169EE7",  # 40 hex: public
    "ref 9f86d081884c7d659a2feaa0c55ad015",  # 32 hex
    "x" + api_key()[:62],  # 63 characters
    api_key() + "x",  # 65 characters
    "z" * 64,  # no digit: not key-shaped
    f"the {seed(11)}",
    f"{seed(5)} zz yy {seed(7)}",  # two non-list words in a row break the run
    "Can you explain what an order book spread and market depth mean for this price?",
])
def test_leaves_lookalikes_alone(text):
    assert not redact_secrets(text).found


def test_no_false_positives_on_any_eval_question():
    questions = [c["question"] for path in sorted((REPO_DIR / "evals").glob("cases*.yaml"))
                 if path.name != "cases_redteam_regressions.yaml"  # generated attacks; base64 can look like a key
                 for c in yaml.safe_load(path.read_text()) if not c["category"].startswith("secret_paste")]
    assert len(questions) > 200
    assert [q for q in questions if redact_secrets(q).found] == []


def test_warning_names_what_was_found_and_what_to_do():
    w = secret_warning(["api_key", "seed_phrase"])
    assert "an exchange API key or secret and a wallet seed phrase" in w
    assert "Delete that API key" in w and "move the funds to a new wallet" in w
    assert "transaction hash" in secret_warning(["private_key"]) and "Revoke" in secret_warning(["pem_private_key"])
    assert not any(ch.isdigit() for ch in w)  # nothing for the number verifier to trip on


# ------------------------------------------------------------- the raw secret goes nowhere

SECRETS = {
    "api_key": lambda: api_key(),
    "private_key": lambda: "0x" + hex_key(),
    "seed_phrase": lambda: seed(24),
    "pem_private_key": lambda: pem("RSA "),
}


def _pieces(secret: str) -> list[str]:
    """Fragments that would show the secret leaked: each PEM line, every 4 words of a phrase, or the whole key."""
    if "\n" in secret:
        return [line for line in secret.splitlines() if len(line) >= 16]
    words = secret.split()
    return [" ".join(words[i:i + 4]) for i in range(len(words) - 3)] if len(words) > 1 else [secret]


@pytest.mark.parametrize("kind", list(SECRETS))
async def test_raw_secret_never_reaches_llm_logs_traces_caches_or_audit(kind, tmp_path, caplog):
    secret = SECRETS[kind]()
    claude = ScriptedProvider("anthropic", {
        "router": [route_json("concept"), route_json("price", ["BTC"])],
    }, default="A funding rate is a periodic payment between long and short traders [1].")
    s = replay_settings(llm_mode="live", database_url=f"sqlite:///{tmp_path / 't.db'}")
    agent = await Agent.create(s, llm=llm_client(s, claude))
    # Serve market data "live" from the fixtures, so the tool cache is filled the way it is in production.
    toolbox, execute = agent.deps.toolbox, agent.deps.toolbox._execute

    async def from_fixtures(tool, args):
        if tool in LOCAL_TOOLS:
            return await execute(tool, args)
        return toolbox._from_fixture(tool, args, toolbox._load(toolbox._fixture_path(tool, args)))

    toolbox.mode, toolbox._execute = "live", from_fixtures
    caplog.set_level(logging.DEBUG)

    events = []
    for question in (f"What is a funding rate? Here is my key: {secret}", f"What's BTC trading at?\n{secret}"):
        events += [e async for e in agent.stream(question)]
    await agent.aclose()

    with agent.traces.engine.connect() as conn:
        stored = [dict(r) for t in (traces, audit_log) for r in conn.execute(select(t)).mappings()]
    places = {
        "llm prompts": json.dumps(claude.calls),
        "logs": caplog.text + json.dumps([r.getMessage() for r in caplog.records]),
        "trace and audit tables": json.dumps(stored, default=str),
        "semantic cache": json.dumps([(e.question, e.payload) for e in agent.deps.semantic_cache._entries]),
        "tool cache": json.dumps(toolbox.cache._data),
        "responses and stream events": json.dumps([e["response"].model_dump(mode="json") if e["type"] == "final"
                                                   else e for e in events], default=str),
    }
    for place, text in places.items():
        leaked = [p for p in _pieces(secret) if p in text or json.dumps(p)[1:-1] in text]
        assert not leaked, f"secret leaked into {place}"

    # ...and the checks above looked at real data: the redacted question went everywhere instead.
    placeholder = f"[REDACTED_{kind.upper()}]"
    assert placeholder in places["llm prompts"] and placeholder in places["trace and audit tables"]
    assert placeholder in places["semantic cache"] and toolbox.cache._data
    finals = [e["response"] for e in events if e["type"] == "final"]
    assert [f.status for f in finals] == ["ok", "ok"] and all("**Security warning:**" in f.answer for f in finals)
    assert all(placeholder in f.question for f in finals)
    guard = [e for e in events if e["type"] == "step" and e["node"] == "input_guard"][0]
    assert guard["detail"]["detail"]["secrets_redacted"] == [kind]
