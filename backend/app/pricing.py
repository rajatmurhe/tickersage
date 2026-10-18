"""Per-model token prices in USD per 1M tokens, used to cost every request.

Anthropic prices are first-party API list prices (Claude Haiku 5.5's rate applies to
prompts up to 100K tokens, far above anything this app sends). OpenAI prices are
placeholders: check them against OpenAI's pricing page before quoting costs.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# model -> (input $/1M, output $/1M)
PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.00, 20.00),
    "gpt-5": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    # OpenRouter model ids, from OpenRouter's models API on 2026-10-19. Only a fallback: OpenRouter
    # reports the billed cost of every call, and the LLM layer records that instead.
    "anthropic/claude-haiku-5.5": (0.10, 0.50),
    "anthropic/claude-sonnet-5.5": (2.00, 10.00),
    "openai/gpt-5-mini": (0.25, 2.00),
    "openai/gpt-5": (1.25, 10.00),
    # NVIDIA API catalog (build.nvidia.com): the developer tier is free and rate-limited.
    "nvidia/nemotron-3.5-lightning-30b-a3b": (0.0, 0.0),
    "nvidia/nemotron-3-super-120b-a12b": (0.0, 0.0),
    # Groq's free tier (rate-limited). On a paid Groq plan, replace these with its per-token prices.
    "openai/gpt-oss-20b": (0.0, 0.0),
    "openai/gpt-oss-120b": (0.0, 0.0),
}

CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    price = PRICES.get(model)
    if price is None:
        log.warning("no price configured for model %s; cost recorded as 0", model)
        return 0.0
    p_in, p_out = price
    total = (
        input_tokens * p_in
        + cache_write_tokens * p_in * CACHE_WRITE_MULT
        + cache_read_tokens * p_in * CACHE_READ_MULT
        + output_tokens * p_out
    )
    return round(total / 1_000_000, 6)
