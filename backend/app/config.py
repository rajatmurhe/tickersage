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
    llm_mode: Literal["auto", "live", "offline"] = "auto"
    # Unset or blank: "openrouter" when that is the only key, otherwise "anthropic". Always set after validation.
    primary_provider: Literal["anthropic", "openai", "openrouter"] | None = None
    routing_mode: Literal["routed", "single"] = "routed"
    planner_mode: Literal["rules", "llm"] = "rules"

    anthropic_fast_model: str = "claude-haiku-5-5"
    anthropic_strong_model: str = "claude-sonnet-5-5"
    openai_fast_model: str = "gpt-5-mini"
    openai_strong_model: str = "gpt-5"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_fast_model: str = "anthropic/claude-haiku-5.5"
    openrouter_strong_model: str = "anthropic/claude-sonnet-5.5"
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
            # With only an OpenRouter key, Claude-first would fail on every call and then fall back.
            only_openrouter = self.openrouter_api_key and not self.anthropic_api_key and not self.openai_api_key
            self.primary_provider = "openrouter" if only_openrouter else "anthropic"
        return self

    @property
    def llm_live(self) -> bool:
        if self.llm_mode == "offline":
            return False
        if self.llm_mode == "live":
            return True
        return bool(self.anthropic_api_key or self.openai_api_key or self.openrouter_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
