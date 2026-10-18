"""Per-client limits on the chat endpoints, for running the API as a public demo.

Fixed windows of a minute and a UTC day, counted in Redis when REDIS_URL is set (so every
worker and instance shares one count) and in process memory otherwise. A Redis outage
degrades to the in-memory counter instead of failing the request. Off unless
RATE_LIMIT_PER_MINUTE or RATE_LIMIT_PER_DAY is set.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, Request

from .config import Settings

log = logging.getLogger(__name__)

MINUTE = 60
DAY = 86_400  # epoch-aligned, so the daily window resets at midnight UTC


@dataclass(frozen=True)
class Limit:
    name: str
    max_requests: int
    window_s: int


class Counter(Protocol):
    async def hit(self, key: str, window_s: int) -> tuple[int, float]: ...


class MemoryCounter:
    """Fixed-window counters in a dict: right for one process, not shared between workers."""

    def __init__(self, clock: Callable[[], float] = time.time, max_keys: int = 50_000) -> None:
        self.clock = clock
        self._max = max_keys
        self._counts: dict[str, tuple[int, int]] = {}  # key -> (window end, hits)

    async def hit(self, key: str, window_s: int) -> tuple[int, float]:
        """Counts one request. Returns (hits so far in this window, seconds until the window resets)."""
        now = self.clock()
        end = (int(now // window_s) + 1) * window_s
        ends, hits = self._counts.get(key, (end, 0))
        if ends != end:
            hits = 0
        if len(self._counts) >= self._max:
            self._counts = {k: v for k, v in self._counts.items() if v[0] > now}
        self._counts[key] = (end, hits + 1)
        return hits + 1, end - now


class RedisCounter:
    def __init__(self, url: str, clock: Callable[[], float] = time.time) -> None:
        import redis.asyncio as redis

        self.clock = clock
        self._redis = redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)
        self._fallback = MemoryCounter(clock)
        self._down_until = 0.0

    async def hit(self, key: str, window_s: int) -> tuple[int, float]:
        if time.monotonic() >= self._down_until:
            now = self.clock()
            end = (int(now // window_s) + 1) * window_s
            redis_key = f"ts:rl:{key}:{end}"
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    pipe.incr(redis_key)
                    pipe.expire(redis_key, window_s + 60)
                    hits, _ = await pipe.execute()
                return int(hits), end - now
            except Exception as exc:  # pragma: no cover - depends on a live Redis
                log.warning("redis unavailable, counting rate limits in memory for 30s: %s", exc)
                self._down_until = time.monotonic() + 30
        return await self._fallback.hit(key, window_s)


class RateLimiter:
    def __init__(self, limits: list[Limit], counter: Counter, trust_proxy: bool = False, proxy_hops: int = 1) -> None:
        self.limits = limits
        self.counter = counter
        self.trust_proxy = trust_proxy
        self.proxy_hops = max(1, proxy_hops)

    @classmethod
    def from_settings(cls, s: Settings) -> RateLimiter | None:
        limits = [Limit("minute", s.rate_limit_per_minute, MINUTE), Limit("day", s.rate_limit_per_day, DAY)]
        limits = [lim for lim in limits if lim.max_requests > 0]
        if not limits:
            return None
        counter: Counter = RedisCounter(s.redis_url) if s.redis_url else MemoryCounter()
        return cls(limits, counter, s.trust_proxy, s.trusted_proxy_hops)

    def client_ip(self, request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        if not self.trust_proxy:
            return peer
        hops = [h.strip() for h in ",".join(request.headers.getlist("x-forwarded-for")).split(",") if h.strip()]
        return hops[-min(self.proxy_hops, len(hops))] if hops else peer

    async def check(self, request: Request) -> None:
        """Counts the request against each window, shortest first; raises 429 once one is used up.

        A request refused by the minute window is not counted against the day.
        """
        ip = self.client_ip(request)
        for lim in self.limits:
            hits, reset_in = await self.counter.hit(f"{lim.name}:{ip}", lim.window_s)
            if hits > lim.max_requests:
                retry = max(1, math.ceil(reset_in))
                raise HTTPException(
                    429,
                    detail=f"Rate limit reached: this demo allows {lim.max_requests} questions per {lim.name}. "
                           f"Try again in {_duration(retry)}.",
                    headers={"Retry-After": str(retry)},
                )


def _duration(seconds: int) -> str:
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{round(seconds / 60)} min"
    return f"{round(seconds / 3600)} h"
