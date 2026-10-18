"""Provider abstraction: model routing, provider fallback and per-call cost.

`LLMClient.generate(tier=...)` picks the model for the tier ("fast" for routing and
simple lookups, "strong" for analysis) on the primary provider, and retries on the
other provider if the call errors, times out, is refused or is truncated. Every
fallback is logged and surfaced in the trace.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .config import Settings
from .pricing import cost_usd

log = logging.getLogger("tickersage.llm")

Tier = Literal["fast", "strong"]


class ProviderError(Exception):
    pass


class LLMUnavailable(Exception):
    def __init__(self, attempts: list[str]) -> None:
        super().__init__("; ".join(attempts) or "no LLM provider configured")
        self.attempts = attempts


@dataclass
class Usage:
    input_tokens: int = 0  # uncached input only, as Anthropic counts it
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    # The amount the provider says it billed (OpenRouter); when set it replaces the price-table estimate.
    reported_cost_usd: float | None = None


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    usage: Usage
    cost_usd: float
    latency_ms: float
    fell_back: bool = False
    failed_attempts: list[str] = field(default_factory=list)


class Provider(Protocol):
    name: str

    async def generate(
        self, *, model: str, system: str, user: str, max_tokens: int,
        json_schema: dict[str, Any] | None, effort: str | None,
    ) -> tuple[str, Usage, str]: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, timeout: float, server_fallback: bool) -> None:
        import anthropic

        self._anthropic = anthropic
        kwargs: dict[str, Any] = {"timeout": timeout, "max_retries": 1}
        if api_key:
            kwargs["api_key"] = api_key
        self.client = anthropic.AsyncAnthropic(**kwargs)
        self.server_fallback = server_fallback

    async def generate(self, *, model, system, user, max_tokens, json_schema, effort):
        a = self._anthropic
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            # The system prompt is static, so it is the cache prefix.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
        }
        output_config: dict[str, Any] = {}
        if effort:
            output_config["effort"] = effort
        if json_schema:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        if output_config:
            kwargs["output_config"] = output_config
        try:
            if self.server_fallback and model.startswith("claude-sonnet-5-5"):
                # Server-side refusal fallback (Claude API only): a declined request is
                # re-run on Anthropic's recommended fallback model inside the same call.
                resp = await self.client.beta.messages.create(
                    **kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
                )
            else:
                resp = await self.client.messages.create(**kwargs)
        except a.APITimeoutError as exc:
            raise ProviderError(f"timeout: {exc}") from exc
        except a.RateLimitError as exc:
            raise ProviderError(f"rate limited: {exc.message}") from exc
        except a.APIStatusError as exc:
            raise ProviderError(f"HTTP {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise ProviderError(f"connection error: {exc}") from exc
        if resp.stop_reason == "refusal":
            category = getattr(resp.stop_details, "category", None) if resp.stop_details else None
            raise ProviderError(f"refusal ({category})")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("response truncated at max_tokens")
        text = "".join(b.text for b in resp.content if b.type == "text")
        u = resp.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )
        return text, usage, resp.model


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, timeout: float) -> None:
        import openai

        self._openai = openai
        self.client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=1)

    async def generate(self, *, model, system, user, max_tokens, json_schema, effort):
        o = self._openai
        kwargs: dict[str, Any] = {"model": model, "instructions": system, "input": user,
                                  "max_output_tokens": max_tokens}
        if json_schema:
            kwargs["text"] = {"format": {"type": "json_schema", "name": "output", "schema": json_schema,
                                         "strict": True}}
        try:
            resp = await self.client.responses.create(**kwargs)
        except o.APITimeoutError as exc:
            raise ProviderError(f"timeout: {exc}") from exc
        except o.APIStatusError as exc:
            raise ProviderError(f"HTTP {exc.status_code}: {exc.message}") from exc
        except o.APIConnectionError as exc:
            raise ProviderError(f"connection error: {exc}") from exc
        if getattr(resp, "status", "completed") not in ("completed", None):
            raise ProviderError(f"response status {resp.status}")
        u = resp.usage
        usage = Usage(input_tokens=getattr(u, "input_tokens", 0) or 0, output_tokens=getattr(u, "output_tokens", 0) or 0)
        return resp.output_text, usage, model


class OpenRouterProvider:
    """OpenRouter's OpenAI-compatible Chat Completions API. Same Claude models, one key.

    Not available through it: the Claude API's server-side refusal fallback. Cost comes from
    the `cost` OpenRouter reports for each call, so traces show what was actually billed.
    """

    name = "openrouter"

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        import openai

        self._openai = openai
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=1)

    async def generate(self, *, model, system, user, max_tokens, json_schema, effort):
        system_msg: dict[str, Any] = {"role": "system", "content": system}
        if model.startswith("anthropic/"):
            # OpenRouter passes Anthropic cache breakpoints through; the system prompt is the static prefix.
            system_msg["content"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [system_msg, {"role": "user", "content": user}],
        }
        extra: dict[str, Any] = {"usage": {"include": True}}
        if effort:
            extra["reasoning"] = {"effort": effort, "exclude": True}
        if json_schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "output", "strict": True, "schema": json_schema}}
            # Only route to upstream providers that honour structured outputs.
            extra["provider"] = {"require_parameters": True}
        resp = await _chat_completion(self.client, self._openai, kwargs, extra)
        return _parse_chat_completion(resp, model)


class NvidiaProvider:
    """NVIDIA's API catalog (build.nvidia.com): Nemotron models behind an OpenAI-compatible Chat Completions API.

    The developer tier is free and rate-limited, so calls are recorded at $0.
    """

    name = "nvidia"

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        import openai

        self._openai = openai
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=1)

    async def generate(self, *, model, system, user, max_tokens, json_schema, effort):
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "output", "strict": True, "schema": json_schema}}
        extra: dict[str, Any] | None = None
        if effort == "low":
            # Nemotron thinks at length by default: ~1,700 tokens and 16 s to classify one question.
            # With thinking off the same call is ~20 tokens and 1 s.
            extra = {"chat_template_kwargs": {"enable_thinking": False}}
        elif effort:
            kwargs["reasoning_effort"] = effort
        resp = await _chat_completion(self.client, self._openai, kwargs, extra)
        return _parse_chat_completion(resp, model)


async def _chat_completion(client: Any, o: Any, kwargs: dict[str, Any], extra: dict[str, Any] | None) -> Any:
    try:
        return await client.chat.completions.create(**kwargs, extra_body=extra)
    except o.APITimeoutError as exc:
        raise ProviderError(f"timeout: {exc}") from exc
    except o.APIStatusError as exc:
        raise ProviderError(f"HTTP {exc.status_code}: {exc.message}") from exc
    except o.APIConnectionError as exc:
        raise ProviderError(f"connection error: {exc}") from exc


_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)


def _parse_chat_completion(resp: Any, model: str) -> tuple[str, Usage, str]:
    """Text, usage and served model from an OpenAI-style chat completion, with failures as ProviderError."""
    error = getattr(resp, "error", None)
    if error or not resp.choices:
        raise ProviderError(f"no completion: {error or 'empty choices'}")
    choice = resp.choices[0]
    native = getattr(choice, "native_finish_reason", None)
    if choice.finish_reason == "content_filter" or native == "refusal":
        raise ProviderError(f"refusal ({native or choice.finish_reason})")
    if choice.finish_reason == "length":
        raise ProviderError("response truncated at max_tokens")
    # Reasoning models may put their thinking inline; only the answer after it is kept.
    text = _THINK_BLOCK.sub("", choice.message.content or "")
    if choice.finish_reason == "error" or not text.strip():
        raise ProviderError(f"no answer text (finish_reason={choice.finish_reason})")
    u = resp.usage
    prompt = getattr(u, "prompt_tokens", 0) or 0
    details = getattr(u, "prompt_tokens_details", None)
    cache_read = getattr(details, "cached_tokens", 0) or 0
    cache_write = getattr(details, "cache_write_tokens", 0) or 0
    cost = getattr(u, "cost", None)
    usage = Usage(
        input_tokens=max(prompt - cache_read - cache_write, 0),  # OpenAI-style prompt_tokens include cached ones
        output_tokens=getattr(u, "completion_tokens", 0) or 0,
        cache_write_tokens=cache_write,
        cache_read_tokens=cache_read,
        reported_cost_usd=float(cost) if cost is not None else None,
    )
    return text, usage, resp.model or model


class LLMClient:
    def __init__(self, settings: Settings, providers: dict[str, Provider] | None = None) -> None:
        self.settings = settings
        self.providers: dict[str, Provider] = providers if providers is not None else self._build(settings)

    @staticmethod
    def _build(s: Settings) -> dict[str, Provider]:
        providers: dict[str, Provider] = {}
        if s.anthropic_api_key or (s.llm_mode == "live" and s.primary_provider == "anthropic"):
            providers["anthropic"] = AnthropicProvider(s.anthropic_api_key, s.llm_timeout_s, s.anthropic_server_fallback)
        if s.openai_api_key:
            providers["openai"] = OpenAIProvider(s.openai_api_key, s.llm_timeout_s)
        if s.openrouter_api_key:
            providers["openrouter"] = OpenRouterProvider(s.openrouter_api_key, s.openrouter_base_url, s.llm_timeout_s)
        if s.nvidia_api_key:
            providers["nvidia"] = NvidiaProvider(s.nvidia_api_key, s.nvidia_base_url, s.llm_timeout_s)
        return providers

    @property
    def available(self) -> bool:
        return bool(self.providers)

    def model_for(self, provider: str, tier: Tier) -> str:
        s = self.settings
        if s.routing_mode == "single":
            tier = "strong"
        table = {
            ("anthropic", "fast"): s.anthropic_fast_model,
            ("anthropic", "strong"): s.anthropic_strong_model,
            ("openai", "fast"): s.openai_fast_model,
            ("openai", "strong"): s.openai_strong_model,
            ("openrouter", "fast"): s.openrouter_fast_model,
            ("openrouter", "strong"): s.openrouter_strong_model,
            ("nvidia", "fast"): s.nvidia_fast_model,
            ("nvidia", "strong"): s.nvidia_strong_model,
        }
        return table[(provider, tier)]

    def _order(self) -> list[str]:
        primary = self.settings.primary_provider
        return [primary] + [p for p in ("anthropic", "openai", "openrouter", "nvidia") if p != primary]

    async def generate(
        self,
        tier: Tier,
        system: str,
        user: str,
        *,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 4000,
        effort: str | None = "low",
    ) -> LLMResponse:
        failed: list[str] = []
        for name in self._order():
            provider = self.providers.get(name)
            if provider is None:
                continue
            model = self.model_for(name, tier)
            started = time.perf_counter()
            try:
                text, usage, served = await asyncio.wait_for(
                    provider.generate(model=model, system=system, user=user, max_tokens=max_tokens,
                                      json_schema=json_schema,
                                      effort=effort if name in ("anthropic", "openrouter", "nvidia") else None),
                    timeout=self.settings.llm_timeout_s + 5,
                )
            except (ProviderError, TimeoutError) as exc:
                msg = f"{name}/{model}: {exc or 'timeout'}"
                log.warning("LLM call failed, trying next provider: %s", msg)
                failed.append(msg)
                continue
            latency = (time.perf_counter() - started) * 1000
            if failed:
                log.warning("LLM fallback used: served by %s/%s after %s", name, served, failed)
            return LLMResponse(
                text=text,
                model=served,
                provider=name,
                usage=usage,
                cost_usd=usage.reported_cost_usd if usage.reported_cost_usd is not None else
                cost_usd(served, usage.input_tokens, usage.output_tokens, usage.cache_write_tokens,
                         usage.cache_read_tokens),
                latency_ms=round(latency, 1),
                fell_back=bool(failed),
                failed_attempts=failed,
            )
        raise LLMUnavailable(failed)
