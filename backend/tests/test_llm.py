"""LLM layer: routing by tier, provider fallback, cost, and the real SDK request shapes."""

import json

import anthropic
import httpx
import httpx2
import openai
import pytest

from app.config import Settings
from app.llm import AnthropicProvider, LLMClient, LLMUnavailable, OpenAIProvider, ProviderError
from app.pricing import cost_usd

from .conftest import ScriptedProvider


def s(**kw) -> Settings:
    return Settings(llm_mode="live", database_url="sqlite://", **kw)


def test_cost_table():
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 100_000) == pytest.approx(2.0 + 1.0)
    assert cost_usd("claude-haiku-5-5", 10_000, 1_000) == pytest.approx(0.001 + 0.0005)
    assert cost_usd("claude-sonnet-5-5", 0, 0, cache_read_tokens=1_000_000) == pytest.approx(0.2)
    assert cost_usd("unknown-model", 1000, 1000) == 0.0


async def test_tier_routing():
    p = ScriptedProvider("anthropic")
    client = LLMClient(s(routing_mode="routed"), {"anthropic": p})
    await client.generate("fast", "sys", "u")
    await client.generate("strong", "sys", "u")
    assert [c["model"] for c in p.calls] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    single = LLMClient(s(routing_mode="single"), {"anthropic": p})
    await single.generate("fast", "sys", "u")
    assert p.calls[-1]["model"] == "claude-sonnet-5-5"


async def test_provider_fallback_is_recorded():
    claude = ScriptedProvider("anthropic", default=ProviderError("HTTP 529: overloaded"))
    gpt = ScriptedProvider("openai", default="fallback answer")
    resp = await LLMClient(s(), {"anthropic": claude, "openai": gpt}).generate("strong", "sys", "u")
    assert resp.text == "fallback answer" and resp.provider == "openai" and resp.fell_back
    assert "overloaded" in resp.failed_attempts[0]
    assert gpt.calls[0]["effort"] is None  # effort is an Anthropic parameter


async def test_all_providers_failing_raises():
    claude = ScriptedProvider("anthropic", default=ProviderError("timeout"))
    with pytest.raises(LLMUnavailable):
        await LLMClient(s(), {"anthropic": claude}).generate("fast", "sys", "u")


async def test_openai_primary_order():
    claude, gpt = ScriptedProvider("anthropic"), ScriptedProvider("openai")
    await LLMClient(s(primary_provider="openai"), {"anthropic": claude, "openai": gpt}).generate("fast", "s", "u")
    assert gpt.calls and not claude.calls


# ------------------------------------------------- real SDKs, mocked HTTP


def _anthropic_message(stop_reason="end_turn", text="hello", model="claude-sonnet-5-5"):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 80, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0},
    }


def _anthropic_provider(handler, server_fallback=True) -> tuple[AnthropicProvider, list]:
    seen: list = []

    def wrapped(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    provider = AnthropicProvider("sk-test", 10, server_fallback)
    provider.client = anthropic.AsyncAnthropic(
        api_key="sk-test", base_url="https://api.anthropic.test", max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(wrapped)),
    )
    return provider, seen


async def test_anthropic_request_shape_with_server_fallback():
    provider, seen = _anthropic_provider(lambda r: httpx2.Response(200, json=_anthropic_message()))
    text, usage, model = await provider.generate(model="claude-sonnet-5-5", system="SYS", user="Q", max_tokens=8000,
                                                 json_schema=None, effort="low")
    body = json.loads(seen[0].content)
    assert text == "hello" and usage.input_tokens == 1200 and model == "claude-sonnet-5-5"
    assert body["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in seen[0].headers["anthropic-beta"]
    assert body["output_config"] == {"effort": "low"}
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in body and "thinking" not in body


async def test_anthropic_structured_output_on_haiku_has_no_fallbacks():
    provider, seen = _anthropic_provider(lambda r: httpx2.Response(200, json=_anthropic_message(text="{}")))
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    await provider.generate(model="claude-haiku-5-5", system="S", user="U", max_tokens=2000, json_schema=schema,
                            effort="low")
    body = json.loads(seen[0].content)
    assert "fallbacks" not in body and "anthropic-beta" not in seen[0].headers
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": schema}


@pytest.mark.parametrize(
    "response,needle",
    [
        (lambda r: httpx2.Response(200, json=_anthropic_message(stop_reason="refusal")), "refusal"),
        (lambda r: httpx2.Response(200, json=_anthropic_message(stop_reason="max_tokens")), "truncated"),
        (lambda r: httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error",
                                                                           "message": "Overloaded"}}), "529"),
    ],
)
async def test_anthropic_failures_become_provider_errors(response, needle):
    provider, _ = _anthropic_provider(response)
    with pytest.raises(ProviderError, match=needle):
        await provider.generate(model="claude-haiku-5-5", system="S", user="U", max_tokens=100, json_schema=None,
                                effort=None)


async def test_openai_request_shape():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "id": "resp_1", "object": "response", "created_at": 0, "model": "gpt-5-mini", "status": "completed",
            "output": [{"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "{\"intent\": \"price\"}", "annotations": []}]}],
            "usage": {"input_tokens": 50, "output_tokens": 7, "total_tokens": 57,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
            "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        })

    provider = OpenAIProvider("sk-test", 10)
    provider.client = openai.AsyncOpenAI(api_key="sk-test", base_url="https://api.openai.test/v1", max_retries=0,
                                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    schema = {"type": "object", "properties": {"intent": {"type": "string"}}, "required": ["intent"],
              "additionalProperties": False}
    text, usage, _ = await provider.generate(model="gpt-5-mini", system="S", user="U", max_tokens=500,
                                             json_schema=schema, effort=None)
    body = json.loads(seen[0].content)
    assert json.loads(text) == {"intent": "price"} and usage.output_tokens == 7
    assert body["instructions"] == "S" and body["text"]["format"]["strict"] is True
