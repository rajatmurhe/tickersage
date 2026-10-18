"""Small async cache layer: Redis when configured, otherwise an in-process TTL dict.

Market data uses short TTLs (10s tickers, 60s candles). A Redis outage degrades to
the in-memory cache instead of failing the request.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol

log = logging.getLogger(__name__)

TTL_TICKER = 10
TTL_KLINES = 60
TTL_DEPTH = 5
TTL_NEWS = 300
TTL_EXCHANGE_INFO = 24 * 3600


class Cache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: float) -> None: ...


class NullCache:
    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any, ttl: float) -> None:
        return None


class MemoryCache:
    def __init__(self, max_items: int = 5000) -> None:
        self._data: dict[str, tuple[float, str]] = {}
        self._max = max_items

    async def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, raw = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        return json.loads(raw)

    async def set(self, key: str, value: Any, ttl: float) -> None:
        if len(self._data) >= self._max:
            # Drop the entry closest to expiry; cheap and good enough for a dev cache.
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)
        self._data[key] = (time.monotonic() + ttl, json.dumps(value, default=str))


class RedisCache:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
        self._fallback = MemoryCache()
        self._down_until = 0.0

    async def get(self, key: str) -> Any | None:
        if time.monotonic() < self._down_until:
            return await self._fallback.get(key)
        try:
            raw = await self._redis.get(f"ts:{key}")
        except Exception as exc:  # pragma: no cover - depends on a live Redis
            self._mark_down(exc)
            return await self._fallback.get(key)
        return None if raw is None else json.loads(raw)

    async def set(self, key: str, value: Any, ttl: float) -> None:
        if time.monotonic() < self._down_until:
            await self._fallback.set(key, value, ttl)
            return
        try:
            await self._redis.set(f"ts:{key}", json.dumps(value, default=str), ex=max(1, int(ttl)))
        except Exception as exc:  # pragma: no cover - depends on a live Redis
            self._mark_down(exc)
            await self._fallback.set(key, value, ttl)

    def _mark_down(self, exc: Exception) -> None:
        log.warning("redis unavailable, using in-memory cache for 30s: %s", exc)
        self._down_until = time.monotonic() + 30


def build_cache(enabled: bool, redis_url: str | None) -> Cache:
    if not enabled:
        return NullCache()
    if redis_url:
        return RedisCache(redis_url)
    return MemoryCache()
