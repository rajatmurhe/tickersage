"""Text embeddings for docs retrieval and the semantic answer cache.

`HashingEmbedder` needs no model download or API key: it hashes unigrams and bigrams
into a fixed-size vector, so similarity is lexical rather than semantic. It keeps the
retrieval path (including pgvector) runnable anywhere, and it is what tests and CI use.
`LocalEmbedder` runs a sentence-transformers model on CPU through fastembed (ONNX, no torch,
no API key): EMBEDDING_PROVIDER=local. EMBEDDING_PROVIDER=openai uses OpenAI's API.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Protocol

import numpy as np

DIM = 384
HASH_DIM = 2048  # wider than DIM to keep hash collisions rare in the lexical embedder

# Crypto shorthand -> the words the docs use.
SYNONYMS = {
    "perp": "perpetual futures", "perps": "perpetual futures", "mcap": "market cap", "marketcap": "market cap",
    "ob": "order book", "orderbook": "order book", "vol": "volatility", "dd": "drawdown", "liq": "liquidation",
    "liqs": "liquidation", "oi": "open interest", "pos": "proof of stake", "pow": "proof of work",
    "ta": "technical analysis", "sma": "moving average", "ema": "moving average", "ma": "moving average",
    "klines": "candlestick", "kline": "candlestick", "candles": "candlestick", "bps": "basis points",
}

_STOP = set(
    """a an the is are was were be been of to in on for and or with what whats what's how does do did
    why when which who this that it its can could would should i me my you your we our they their
    about tell explain mean means meaning define definition simple terms please there here some any
    as at by from into than then so if have has had get gets use used using work works working
    difference between vs versus like just really actually s"""
    .split()
)


class Embedder(Protocol):
    dim: int
    name: str

    async def embed(self, texts: list[str]) -> np.ndarray: ...

    async def embed_query(self, text: str) -> np.ndarray: ...


def tokens(text: str) -> list[str]:
    out = []
    words = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        words.extend(SYNONYMS.get(word, word).split())
    for tok in words:
        if tok in _STOP:
            continue
        if len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


class HashingEmbedder:
    dim = HASH_DIM
    name = "hash-2048"

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        toks = tokens(text)
        feats: list[tuple[str, float]] = [(t, 1.0) for t in toks]
        feats += [(f"{a}_{b}", 1.5) for a, b in zip(toks, toks[1:], strict=False)]
        # 4-letter prefixes let "funding"/"fund" and "volatile"/"volatility" meet.
        feats += [(f"^{t[:4]}", 0.5) for t in toks if len(t) >= 5]
        for feat, weight in feats:
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
            vec[h % self.dim] += weight if (h >> 63) & 1 else -weight
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm else vec

    async def embed(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._vector(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    async def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


class LocalEmbedder:
    """A sentence-transformers model on CPU via fastembed. bge-small-en-v1.5: 384 dims, ~130 MB download once."""

    # bge models are trained with this instruction on the query side only.
    BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model, cache_dir=cache_dir)
        self.name = model
        self.dim = int(self._model.embedding_size)
        self._prefix = self.BGE_QUERY_PREFIX if "bge" in model.lower() else ""

    def _embed_sync(self, texts: list[str]) -> np.ndarray:
        arr = np.array(list(self._model.embed(texts, batch_size=64)), dtype=np.float32)
        return arr / np.linalg.norm(arr, axis=1, keepdims=True)

    async def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        return await asyncio.to_thread(self._embed_sync, texts)

    async def embed_query(self, text: str) -> np.ndarray:
        return (await self.embed([self._prefix + text]))[0]


class OpenAIEmbedder:
    dim = DIM
    name = "openai-text-embedding-3-small-384"

    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def embed(self, texts: list[str]) -> np.ndarray:
        resp = await self._client.embeddings.create(model=self._model, input=texts, dimensions=self.dim)
        arr = np.array([d.embedding for d in resp.data], dtype=np.float32)
        return arr / np.linalg.norm(arr, axis=1, keepdims=True)

    async def embed_query(self, text: str) -> np.ndarray:
        return (await self.embed([text]))[0]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / denom) if denom and not math.isnan(denom) else 0.0
