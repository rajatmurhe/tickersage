"""Provider abstraction: model routing, provider fallback and per-call cost.

`LLMClient.generate(tier=...)` picks the model for the tier ("fast" for routing and
simple lookups, "strong" for analysis) on the primary provider, and retries on the
other provider if the call errors, times out, is refused or is truncated. Every
fallback is logged and surfaced in the trace.
"""

from __future__ import annotations

import asyncio
import logging
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
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


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
        }
        return table[(provider, tier)]

    def _order(self) -> list[str]:
        primary = self.settings.primary_provider
        return [primary] + [p for p in ("anthropic", "openai") if p != primary]

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
                                      json_schema=json_schema, effort=effort if name == "anthropic" else None),
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
                cost_usd=cost_usd(served, usage.input_tokens, usage.output_tokens,
                                  usage.cache_write_tokens, usage.cache_read_tokens),
                latency_ms=round(latency, 1),
                fell_back=bool(failed),
                failed_attempts=failed,
            )
        raise LLMUnavailable(failed)
