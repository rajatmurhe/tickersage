"""Semantic answer cache for concept questions.

Only concept answers are cached: they come from static docs, so a cached answer
can't go stale the way a price can. Lookups compare question embeddings by cosine
similarity against a threshold.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from .embeddings import Embedder


@dataclass
class _Entry:
    vector: np.ndarray
    question: str
    payload: dict[str, Any]
    expires: float


class SemanticCache:
    def __init__(self, embedder: Embedder, threshold: float = 0.92, ttl_s: float = 24 * 3600, max_items: int = 500):
        self.embedder = embedder
        self.threshold = threshold
        self.ttl_s = ttl_s
        self.max_items = max_items
        self._entries: list[_Entry] = []

    async def lookup(self, question: str) -> tuple[dict[str, Any], float, str] | None:
        now = time.monotonic()
        self._entries = [e for e in self._entries if e.expires > now]
        if not self._entries:
            return None
        q = (await self.embedder.embed([question]))[0]
        best, best_score = None, -1.0
        for e in self._entries:
            score = float(e.vector @ q)
            if score > best_score:
                best, best_score = e, score
        if best is not None and best_score >= self.threshold:
            return best.payload, round(best_score, 3), best.question
        return None

    async def store(self, question: str, payload: dict[str, Any]) -> None:
        vec = (await self.embedder.embed([question]))[0]
        if len(self._entries) >= self.max_items:
            self._entries.pop(0)
        self._entries.append(_Entry(vec, question, payload, time.monotonic() + self.ttl_s))
