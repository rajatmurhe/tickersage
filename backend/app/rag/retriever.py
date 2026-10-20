"""Hybrid retrieval: BM25 and vector search, fused with reciprocal rank fusion, then reranked.

    candidates = BM25 top 50  +  vector top 50          (vector leg: in memory, or pgvector over SQL)
    fused      = RRF(k=60) over the two rankings
    final      = reranker score over the fused top 20   (cross-encoder, or lexical coverage offline)

`mode` picks a stage for the ablation: "bm25", "vector", "hybrid" (fusion, no rerank) or "hybrid_rerank".
Each mode has its own "no answer" rule, so an off-topic question returns no chunks instead of the least bad
ones: the top score has to clear a threshold (set on the dev split only; see evals/experiments.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..embeddings import Embedder
from .bm25 import BM25
from .corpus import Chunk, Corpus
from .rerank import Reranker

log = logging.getLogger(__name__)

MODES = ("bm25", "vector", "hybrid", "hybrid_rerank")
RRF_K = 60
CANDIDATES = 50
RERANK_TOP = 20


@dataclass
class Thresholds:
    """Minimum top score for an answer, per mode. Defaults were picked on the dev split (evals/rag)."""

    bm25: float = 9.57
    vector: float = 0.128
    rerank: float = 0.29


@dataclass
class Hit:
    chunk: Chunk
    score: float
    signals: dict[str, float] = field(default_factory=dict)


class Retriever:
    def __init__(self, corpus: Corpus, embedder: Embedder, reranker: Reranker | None = None, *,
                 mode: str = "hybrid_rerank", store: VectorStore | None = None,
                 thresholds: Thresholds | None = None, cache_dir: Path | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode == "hybrid_rerank" and reranker is None:
            raise ValueError("hybrid_rerank needs a reranker")
        self.corpus = corpus
        self.chunks = corpus.chunks
        self.by_id = {c.id: i for i, c in enumerate(self.chunks)}
        self.embedder = embedder
        self.reranker = reranker
        self.mode = mode
        self.thresholds = thresholds or Thresholds()
        self.store = store or MemoryStore()
        self.cache_dir = cache_dir
        self.bm25 = BM25([c.index_text for c in self.chunks])
        self._built = False

    async def build(self) -> None:
        if self.mode != "bm25":
            await self.store.sync(self.chunks, self.embedder, self.cache_dir)
        self._built = True

    async def search(self, query: str, k: int = 3) -> list[Hit]:
        """Top-k hits, or [] when the best one doesn't clear this mode's threshold."""
        ranked = await self.rank(query, max(k, 10))
        return [h for h in ranked[:k] if not self._abstain(h)] if ranked and not self._abstain(ranked[0]) else []

    async def rank(self, query: str, n: int = 10) -> list[Hit]:
        """The full ranking before any abstain decision (what recall@k and MRR are computed on)."""
        if not self._built:
            await self.build()
        lex = self.bm25.search(query, CANDIDATES)
        if self.mode == "bm25":
            return [Hit(self.chunks[i], s / (s + 10), {"bm25": s}) for i, s in lex[:n]]
        qvec = await self.embedder.embed_query(query)
        vec = [(self.by_id[cid], s) for cid, s in await self.store.search(qvec, CANDIDATES) if cid in self.by_id]
        if self.mode == "vector":
            return [Hit(self.chunks[i], s, {"cos": s}) for i, s in vec[:n]]

        bm25_of, cos_of = dict(lex), dict(vec)
        fused: dict[int, float] = {}
        for ranking in (lex, vec):
            for rank, (i, _) in enumerate(ranking):
                fused[i] = fused.get(i, 0.0) + 1 / (RRF_K + rank + 1)
        top = sorted(fused.items(), key=lambda x: x[1], reverse=True)
        best = 2 / (RRF_K + 1)
        hits = [Hit(self.chunks[i], f / best, {"rrf": f, "bm25": bm25_of.get(i, 0.0), "cos": cos_of.get(i, 0.0)})
                for i, f in top]
        if self.mode == "hybrid":
            return hits[:n]

        pool = hits[:RERANK_TOP]
        scores = await self.reranker.score(query, [h.chunk for h in pool])  # type: ignore[union-attr]
        a = self.reranker.blend  # type: ignore[union-attr]
        for h, s in zip(pool, scores, strict=True):
            h.signals["rerank"] = s
            h.score = a * s + (1 - a) * min(1.0, h.score)
        pool.sort(key=lambda h: (h.score, h.signals["rrf"]), reverse=True)
        return pool[:n]

    def _abstain(self, hit: Hit) -> bool:
        t = self.thresholds
        if self.mode == "bm25":
            return hit.signals["bm25"] < t.bm25
        if self.mode == "vector":
            return hit.signals["cos"] < t.vector
        if self.mode == "hybrid":
            # Fusion has no absolute scale: answer when either leg is confident on this chunk.
            return hit.signals["bm25"] < t.bm25 and hit.signals["cos"] < t.vector
        return hit.signals["rerank"] < t.rerank


# ------------------------------------------------------------------ vector stores


class VectorStore:
    name = "base"

    async def sync(self, chunks: list[Chunk], embedder: Embedder, cache_dir: Path | None) -> None: ...

    async def search(self, qvec: np.ndarray, n: int) -> list[tuple[str, float]]: ...


class MemoryStore(VectorStore):
    """Exact cosine search over an in-memory matrix.

    Model embeddings are cached on disk per model, keyed by each chunk's text hash, so editing the corpus or
    the chunker only re-embeds the chunks that changed.
    """

    name = "memory"

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.matrix: np.ndarray | None = None

    async def sync(self, chunks: list[Chunk], embedder: Embedder, cache_dir: Path | None) -> None:
        self.ids = [c.id for c in chunks]
        texts = [c.index_text for c in chunks]
        if cache_dir is None or embedder.name == "hash-2048":  # the hash embedder is faster than a disk read
            self.matrix = await embedder.embed(texts)
            return
        path = cache_dir / f"emb-{embedder.name.replace('/', '_')}.npz"
        cached: dict[str, np.ndarray] = {}
        if path.exists():
            with np.load(path) as z:
                cached = dict(zip(z["keys"].tolist(), z["vecs"], strict=True))
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        todo = [i for i, k in enumerate(keys) if k not in cached]
        if todo:
            vecs = await embedder.embed([texts[i] for i in todo])
            cached.update({keys[i]: v for i, v in zip(todo, vecs, strict=True)})
            path.parent.mkdir(parents=True, exist_ok=True)
            live = {k: cached[k] for k in keys}  # drop vectors for chunks no longer in the corpus
            np.savez(path, keys=np.array(list(live)), vecs=np.vstack(list(live.values())))
            log.info("embedded %d new chunks with %s (%d cached)", len(todo), embedder.name, len(keys) - len(todo))
        self.matrix = np.vstack([cached[k] for k in keys]).astype(np.float32)

    async def search(self, qvec: np.ndarray, n: int) -> list[tuple[str, float]]:
        assert self.matrix is not None, "sync() first"
        sims = self.matrix @ qvec
        top = np.argsort(-sims)[:n]
        return [(self.ids[i], float(sims[i])) for i in top]


class PgVectorStore(VectorStore):
    """Chunks and embeddings in PostgreSQL + pgvector, searched with an HNSW cosine index.

    Sync is incremental: a chunk is re-embedded only when its text (or the embedding model) changed, and chunks
    no longer in the corpus are deleted. Changing the model rebuilds the table, since dimensions may differ.
    """

    name = "pgvector"
    TABLE = "rag_chunks"
    HNSW_MAX_DIM = 2000  # pgvector's limit for an HNSW index on `vector`; above it, search is an exact scan

    def __init__(self, url: str) -> None:
        self.url = url
        self._conn = None
        self._lock = asyncio.Lock()
        self.dim = 0

    async def _connection(self):
        import psycopg

        if self._conn is None or self._conn.closed:
            self._conn = await psycopg.AsyncConnection.connect(self.url, autocommit=True)
            await self._conn.execute("SET hnsw.ef_search = 100")  # default 40 would cap results below CANDIDATES
        return self._conn

    async def sync(self, chunks: list[Chunk], embedder: Embedder, cache_dir: Path | None) -> None:
        self.dim = embedder.dim
        async with self._lock:
            conn = await self._connection()
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute("CREATE TABLE IF NOT EXISTS rag_meta (key text PRIMARY KEY, value text)")
            cur = await conn.execute("SELECT value FROM rag_meta WHERE key = 'embedder'")
            row = await cur.fetchone()
            model = f"{embedder.name}:{embedder.dim}"
            if row is None or row[0] != model:
                await conn.execute(f"DROP TABLE IF EXISTS {self.TABLE}")
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self.TABLE} (id text PRIMARY KEY, doc_id text, title text, url text, "
                f"license text, section text, body text, content_hash text, embedding vector({embedder.dim}))")
            if embedder.dim <= self.HNSW_MAX_DIM:
                await conn.execute(f"CREATE INDEX IF NOT EXISTS {self.TABLE}_hnsw ON {self.TABLE} "
                                   f"USING hnsw (embedding vector_cosine_ops)")
            await conn.execute(
                "INSERT INTO rag_meta (key, value) VALUES ('embedder', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (model,))

            cur = await conn.execute(f"SELECT id, content_hash FROM {self.TABLE}")
            existing = dict(await cur.fetchall())
            wanted = {c.id: hashlib.sha256(c.index_text.encode()).hexdigest() for c in chunks}
            stale = [cid for cid in existing if cid not in wanted]
            todo = [c for c in chunks if existing.get(c.id) != wanted[c.id]]
            if stale:
                await conn.execute(f"DELETE FROM {self.TABLE} WHERE id = ANY(%s)", (stale,))
            for start in range(0, len(todo), 128):
                batch = todo[start:start + 128]
                vecs = await embedder.embed([c.index_text for c in batch])
                async with conn.cursor() as cur:
                    await cur.executemany(
                        f"INSERT INTO {self.TABLE} (id, doc_id, title, url, license, section, body, content_hash, "
                        f"embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector) ON CONFLICT (id) DO UPDATE SET "
                        f"doc_id=EXCLUDED.doc_id, title=EXCLUDED.title, url=EXCLUDED.url, license=EXCLUDED.license, "
                        f"section=EXCLUDED.section, body=EXCLUDED.body, content_hash=EXCLUDED.content_hash, "
                        f"embedding=EXCLUDED.embedding",
                        [(c.id, c.doc_id, c.title, c.url, c.license, c.section, c.text, wanted[c.id], vec_literal(v))
                         for c, v in zip(batch, vecs, strict=True)])
            log.info("pgvector sync: %d chunks, %d (re)embedded, %d deleted", len(chunks), len(todo), len(stale))

    async def search(self, qvec: np.ndarray, n: int) -> list[tuple[str, float]]:
        lit = vec_literal(qvec)
        async with self._lock:
            conn = await self._connection()
            cur = await conn.execute(
                f"SELECT id, 1 - (embedding <=> %s::vector) FROM {self.TABLE} ORDER BY embedding <=> %s::vector "
                f"LIMIT %s", (lit, lit, n))
            rows = await cur.fetchall()
        return [(r[0], float(r[1])) for r in rows]

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()


def vec_literal(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
