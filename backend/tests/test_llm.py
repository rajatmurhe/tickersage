"""LLM layer: routing by tier, provider fallback, cost, and the real SDK request shapes."""

import json

import anthropic
import httpx
import httpx2
import openai
import pytest

from app.config import Settings
from app.llm import (AnthropicProvider, LLMClient, LLMUnavailable, NvidiaProvider, OpenAIProvider, OpenRouterProvider,
                     ProviderError, Usage)
from app.synthesizer import normalize_citations
from app.pricing import cost_usd

from .conftest import ScriptedProvider


def s(**kw) -> Settings:
    # No .env and no keys, so a developer's local settings can't change what these tests exercise.
    base = dict(_env_file=None, llm_mode="live", database_url="sqlite://", anthropic_api_key=None,
                openai_api_key=None, openrouter_api_key=None, nvidia_api_key=None)
    return Settings(**{**base, **kw})


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


# --- OpenRouter ----------------------------------------------------------------------------------


def _openrouter_completion(**over) -> dict:
    body = {
        "id": "gen-1", "object": "chat.completion", "created": 0, "model": "anthropic/claude-haiku-5.5",
        "choices": [{"index": 0, "finish_reason": "stop", "native_finish_reason": "end_turn",
                     "message": {"role": "assistant", "content": "{\"intent\": \"price\"}"}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230, "cost": 0.000255,
                  "prompt_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 0}},
    }
    body.update(over)
    return body


def _openrouter_provider(handler) -> tuple[OpenRouterProvider, list]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider = OpenRouterProvider("sk-or-test", "https://openrouter.test/api/v1", 10)
    provider.client = openai.AsyncOpenAI(api_key="sk-or-test", base_url="https://openrouter.test/api/v1",
                                         max_retries=0,
                                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(wrapped)))
    return provider, seen


async def test_openrouter_request_shape_and_billed_usage():
    provider, seen = _openrouter_provider(lambda r: httpx.Response(200, json=_openrouter_completion()))
    schema = {"type": "object", "properties": {"intent": {"type": "string"}}, "required": ["intent"],
              "additionalProperties": False}
    text, usage, model = await provider.generate(model="anthropic/claude-haiku-5.5", system="SYS", user="Q",
                                                 max_tokens=2000, json_schema=schema, effort="low")
    req = seen[0]
    body = json.loads(req.content)
    assert req.url.path == "/api/v1/chat/completions"
    assert body["model"] == "anthropic/claude-haiku-5.5" and body["max_tokens"] == 2000
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][1] == {"role": "user", "content": "Q"}
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"] == {"require_parameters": True}
    assert body["reasoning"]["effort"] == "low" and body["usage"] == {"include": True}
    assert json.loads(text) == {"intent": "price"} and model == "anthropic/claude-haiku-5.5"
    # prompt_tokens include cached ones; Usage keeps Anthropic's split, and the billed cost is carried through.
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (200, 1000, 30)
    assert usage.reported_cost_usd == pytest.approx(0.000255)


async def test_openrouter_plain_text_call_for_other_models():
    provider, seen = _openrouter_provider(lambda r: httpx.Response(200, json=_openrouter_completion(model="openai/gpt-5")))
    await provider.generate(model="openai/gpt-5", system="SYS", user="Q", max_tokens=100, json_schema=None,
                            effort=None)
    body = json.loads(seen[0].content)
    assert body["messages"][0] == {"role": "system", "content": "SYS"}  # cache breakpoints are Anthropic-only
    assert "response_format" not in body and "provider" not in body and "reasoning" not in body


@pytest.mark.parametrize("response, needle", [
    (httpx.Response(429, json={"error": {"message": "Rate limit exceeded", "code": 429}}), "HTTP 429"),
    (httpx.Response(200, json=_openrouter_completion(choices=[
        {"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": "{\"int"}}])),
     "truncated"),
    (httpx.Response(200, json=_openrouter_completion(choices=[
        {"index": 0, "finish_reason": "stop", "native_finish_reason": "refusal",
         "message": {"role": "assistant", "content": "I can't help with that."}}])), "refusal"),
    (httpx.Response(200, json=_openrouter_completion(choices=[], error={"message": "upstream failed", "code": 502})),
     "no completion"),
])
async def test_openrouter_failures_become_provider_errors(response, needle):
    provider, _ = _openrouter_provider(lambda r: response)
    with pytest.raises(ProviderError, match=needle):
        await provider.generate(model="anthropic/claude-haiku-5.5", system="S", user="U", max_tokens=100,
                                json_schema=None, effort=None)


async def test_billed_cost_replaces_the_price_table():
    class Billed:
        name = "openrouter"

        async def generate(self, *, model, system, user, max_tokens, json_schema, effort):
            return "ok", Usage(input_tokens=1000, output_tokens=200, reported_cost_usd=0.0123), model

    resp = await LLMClient(s(primary_provider="openrouter"), {"openrouter": Billed()}).generate("strong", "s", "u")
    assert resp.cost_usd == 0.0123 and resp.model == "anthropic/claude-sonnet-5.5" and resp.provider == "openrouter"


def test_openrouter_only_key_makes_it_the_primary():
    only = s(openrouter_api_key="sk-or-test")
    assert only.primary_provider == "openrouter" and only.llm_live
    assert list(LLMClient(only).providers) == ["openrouter"]
    assert LLMClient(only).model_for("openrouter", "fast") == "anthropic/claude-haiku-5.5"
    assert s(openrouter_api_key="sk-or-test", primary_provider="").primary_provider == "openrouter"  # blank in .env
    assert s().primary_provider == "anthropic"
    # An explicit choice wins, and a Claude key keeps Anthropic as the default.
    assert s(openrouter_api_key="sk-or-test", primary_provider="anthropic").primary_provider == "anthropic"
    assert s(openrouter_api_key="sk-or-test", anthropic_api_key="sk-ant-test").primary_provider == "anthropic"


# --- NVIDIA ----------------------------------------------------------------------------------------


def _nvidia_provider(handler) -> tuple[NvidiaProvider, list]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    provider = NvidiaProvider("nvapi-test", "https://nvidia.test/v1", 10)
    provider.client = openai.AsyncOpenAI(api_key="nvapi-test", base_url="https://nvidia.test/v1", max_retries=0,
                                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(wrapped)))
    return provider, seen


def _nvidia_completion(content: str) -> dict:
    return {"id": "chatcmpl-1", "object": "chat.completion", "created": 0, "model": "nvidia/nemotron-3-super-120b-a12b",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 120, "total_tokens": 920}}


async def test_nvidia_request_shape_strips_inline_thinking():
    provider, seen = _nvidia_provider(lambda r: httpx.Response(200, json=_nvidia_completion(
        "<think>The user wants a comparison.</think>\n{\"intent\": \"compare\"}")))
    schema = {"type": "object", "properties": {"intent": {"type": "string"}}, "required": ["intent"],
              "additionalProperties": False}
    text, usage, model = await provider.generate(model="nvidia/nemotron-3-super-120b-a12b", system="SYS", user="Q",
                                                 max_tokens=2000, json_schema=schema, effort="low")
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/chat/completions"
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "Q"}]
    assert body["response_format"]["json_schema"]["schema"] == schema
    assert not {"usage", "provider", "reasoning"} & body.keys()  # OpenRouter-only fields stay out
    assert body["chat_template_kwargs"] == {"enable_thinking": False}  # low effort turns Nemotron's thinking off
    assert json.loads(text) == {"intent": "compare"} and model == "nvidia/nemotron-3-super-120b-a12b"
    assert (usage.input_tokens, usage.output_tokens, usage.reported_cost_usd) == (800, 120, None)


async def test_nvidia_thinking_without_an_answer_is_an_error():
    provider, seen = _nvidia_provider(lambda r: httpx.Response(200, json=_nvidia_completion("<think>hmm</think>  ")))
    with pytest.raises(ProviderError, match="no answer text"):
        await provider.generate(model="nvidia/nemotron-3-super-120b-a12b", system="S", user="U", max_tokens=100,
                                json_schema=None, effort="medium")
    body = json.loads(seen[0].content)
    assert body["reasoning_effort"] == "medium" and "chat_template_kwargs" not in body  # thinking stays on


def test_nvidia_calls_are_free_and_picked_when_it_is_the_only_key():
    assert cost_usd("nvidia/nemotron-3-super-120b-a12b", 1_000_000, 1_000_000) == 0.0
    only = s(nvidia_api_key="nvapi-test")
    assert only.primary_provider == "nvidia" and only.llm_live and list(LLMClient(only).providers) == ["nvidia"]
    client = LLMClient(only)
    assert (client.model_for("nvidia", "fast"), client.model_for("nvidia", "strong")) == (
        "nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3-super-120b-a12b")
    # With both free-tier keys, OpenRouter comes first unless PRIMARY_PROVIDER says otherwise.
    assert s(nvidia_api_key="nvapi-test", openrouter_api_key="sk-or-test").primary_provider == "openrouter"
    assert s(nvidia_api_key="nvapi-test", openrouter_api_key="sk-or-test", primary_provider="nvidia").primary_provider == "nvidia"


def test_normalize_citations():
    assert normalize_citations("Volume was 3.07x SOL's【1】.") == "Volume was 3.07x SOL's[1]."
    assert normalize_citations("See 【2†source】, 【1, 3】, 【 4 】 and 【1，2】.") == "See [2], [1, 3], [4] and [1, 2]."
    assert normalize_citations("Already [1] fine; 【not a cite】 stays.") == "Already [1] fine; 【not a cite】 stays."
