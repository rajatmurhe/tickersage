"""Runtime settings, read from the environment (and an optional .env file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_DIR / ".env", ".env"), extra="ignore")

    # --- LLM providers -------------------------------------------------------
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    # OpenRouter serves the same Claude models (and GPT ones) through one OpenAI-compatible API.
    openrouter_api_key: str | None = None
    # NVIDIA's API catalog (build.nvidia.com): Nemotron models, free for prototyping, OpenAI-compatible.
    nvidia_api_key: str | None = None
    # Groq (console.groq.com): very fast OpenAI-compatible inference with a free, rate-limited tier. Used as
    # the first backup when the primary provider errors or stalls.
    groq_api_key: str | None = None
    llm_mode: Literal["auto", "live", "offline"] = "auto"
    # Unset or blank: picked from the keys (see _pick_primary_provider). Always set after validation.
    primary_provider: Literal["anthropic", "openai", "openrouter", "nvidia", "groq"] | None = None
    routing_mode: Literal["routed", "single"] = "routed"
    planner_mode: Literal["rules", "llm"] = "rules"

    anthropic_fast_model: str = "claude-haiku-5-5"
    anthropic_strong_model: str = "claude-sonnet-5-5"
    openai_fast_model: str = "gpt-5-mini"
    openai_strong_model: str = "gpt-5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_fast_model: str = "anthropic/claude-haiku-5.5"
    openrouter_strong_model: str = "anthropic/claude-sonnet-5.5"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    # Super for routing too: in testing, Lightning stalled on ~1 in 6 calls and 26 times in one 110-case eval.
    nvidia_fast_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nvidia_strong_model: str = "nvidia/nemotron-3-super-120b-a12b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # gpt-oss models: Groq enforces strict JSON schemas on these, which the router relies on.
    groq_fast_model: str = "openai/gpt-oss-20b"
    groq_strong_model: str = "openai/gpt-oss-120b"
    # Server-side refusal fallback on Claude Sonnet 5.5 (Claude API only).
    anthropic_server_fallback: bool = True
    llm_timeout_s: float = 30.0

    # --- Tools ---------------------------------------------------------------
    tool_mode: Literal["live", "replay", "record"] = "live"
    fixtures_dir: Path = REPO_DIR / "evals" / "fixtures"
    tool_timeout_s: float = 5.0
    tool_retries: int = 2
    binance_base_urls: list[str] = ["https://api.binance.com", "https://data-api.binance.vision"]
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    news_rss_url: str = "https://news.google.com/rss/search"
    max_news: int = 5
    max_doc_chunks: int = 3

    # --- Infrastructure ------------------------------------------------------
    redis_url: str | None = None
    cache_enabled: bool = True
    database_url: str = f"sqlite:///{REPO_DIR / 'tickersage.db'}"
    docs_backend: Literal["memory", "pgvector"] = "memory"
    pgvector_url: str | None = None
    embedding_provider: Literal["hash", "openai"] = "hash"
    docs_dir: Path = BACKEND_DIR / "docs_corpus"

    # --- Browser access (the Next.js frontend calls the API cross-origin) ----
    # Any localhost port is allowed for development; add deployed frontend URLs as a JSON list.
    cors_origins: list[str] = []
    cors_origin_regex: str | None = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"

    # --- Public demo protection (everything here is off by default) ----------
    # Questions per client IP on POST /api/chat and /api/chat/stream; 0 = unlimited.
    rate_limit_per_minute: int = 0
    rate_limit_per_day: int = 0
    # Behind a reverse proxy every request comes from the proxy, so read the client from X-Forwarded-For.
    # Proxies append to that header, so the entry `trusted_proxy_hops` from the right is the address the
    # outermost trusted proxy saw; anything left of it was sent by the client and can be forged.
    trust_proxy: bool = False
    trusted_proxy_hops: int = 1
    # Once today's (UTC) traced LLM cost reaches this, answers use offline mode until midnight UTC.
    daily_budget_usd: float | None = None
    # When set, /api/traces and /api/refusals (they show raw user questions) need an X-Admin-Token header.
    admin_token: str | None = None

    # --- Quality switches (used by the experiments) --------------------------
    verifier_enabled: bool = True
    semantic_cache_enabled: bool = True
    semantic_cache_threshold: float = 0.92

    @field_validator("daily_budget_usd", mode="before")
    @classmethod
    def _blank_budget_is_unset(cls, v: object) -> object:
        return None if v == "" else v  # `DAILY_BUDGET_USD=` in .env means no cap

    @field_validator("primary_provider", mode="before")
    @classmethod
    def _blank_provider_is_unset(cls, v: object) -> object:
        return None if v == "" else v  # `PRIMARY_PROVIDER=` in .env means "pick from the keys"

    @model_validator(mode="after")
    def _pick_primary_provider(self) -> "Settings":
        if self.primary_provider is None:
            # Claude first when its (or the OpenAI fallback's) key is set; otherwise the first provider with a
            # key, so a missing Claude key doesn't make every call fail and then fall back.
            if self.anthropic_api_key or self.openai_api_key:
                self.primary_provider = "anthropic"
            elif self.openrouter_api_key:
                self.primary_provider = "openrouter"
            elif self.nvidia_api_key:
                self.primary_provider = "nvidia"
            elif self.groq_api_key:
                self.primary_provider = "groq"
            else:
                self.primary_provider = "anthropic"
        return self

    @property
    def llm_live(self) -> bool:
        if self.llm_mode == "offline":
            return False
        if self.llm_mode == "live":
            return True
        return bool(self.anthropic_api_key or self.openai_api_key or self.openrouter_api_key or self.nvidia_api_key
                    or self.groq_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
