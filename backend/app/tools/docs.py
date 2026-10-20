"""search_docs: hybrid retrieval over the docs corpus, returned as citable chunks.

The corpus is the hand-written glossary plus ~90 fetched public documents (whitepapers, BIPs/EIPs,
ethereum.org and Binance API docs, Wikipedia), each with its source URL and licence; see app/rag/ and
docs_corpus/manifest.yaml. Retrieval is BM25 + vector search fused with RRF and then reranked
(app/rag/retriever.py). Vector backends: in-memory (default, and what CI runs) or PostgreSQL + pgvector;
if pgvector can't be reached at startup the index falls back to memory rather than failing.

Every returned chunk carries its id, title and URL so answers can cite it, and chunks that contain
instructions rather than reference text are withheld before anything downstream sees them.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from ..embeddings import Embedder
from ..guardrails import detect_injection
from ..rag import corpus as rag_corpus
from ..rag.rerank import CrossEncoderReranker, LexicalReranker, Reranker
from ..rag.retriever import MemoryStore, PgVectorStore, Retriever, Thresholds, VectorStore

log = logging.getLogger(__name__)


class DocsInput(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    k: int = Field(default=3, ge=1, le=5)


class DocChunk(BaseModel):
    id: str
    title: str
    url: str
    text: str
    score: float
    section: str = ""
    redacted: bool = False


class DocsOutput(BaseModel):
    query: str
    chunks: list[DocChunk]
    source: str = "TickerSage docs corpus"


def load_corpus(docs_dir: Path) -> list[dict]:
    return [c.as_dict() for c in rag_corpus.load(docs_dir).chunks]


def default_thresholds(embedder: Embedder, reranker: Reranker | None) -> Thresholds:
    """No-answer thresholds per configuration, picked on the dev split only (evals/experiments.md)."""
    t = Thresholds()
    if embedder.name != "hash-2048":
        t.vector = 0.62  # bge-small cosines sit higher than the hash embedder's
    if isinstance(reranker, CrossEncoderReranker):
        t.rerank = 0.10
    return t


class DocsIndex:
    def __init__(self, docs_dir: Path, embedder: Embedder, backend: str = "memory", pg_url: str | None = None, *,
                 reranker: str | Reranker | None = "lexical", mode: str = "hybrid_rerank",
                 cache_dir: Path | None = None, model_cache_dir: str | None = None,
                 thresholds: Thresholds | None = None):
        self.docs_dir = docs_dir
        self.embedder = embedder
        self.backend = backend
        self.pg_url = pg_url
        self.mode = mode
        self.cache_dir = cache_dir
        self._reranker_spec = reranker
        self._model_cache_dir = model_cache_dir
        self._thresholds = thresholds
        self.retriever: Retriever | None = None

    @property
    def chunks(self) -> list:
        return self.retriever.chunks if self.retriever else []

    async def build(self) -> None:
        corpus = rag_corpus.load(self.docs_dir)
        reranker = self._make_reranker(corpus)
        mode = self.mode if reranker is not None or self.mode != "hybrid_rerank" else "hybrid"
        store: VectorStore = MemoryStore()
        if self.backend == "pgvector" and self.pg_url:
            store = PgVectorStore(self.pg_url)
        self.retriever = Retriever(corpus, self.embedder, reranker, mode=mode, store=store,
                                   thresholds=self._thresholds or default_thresholds(self.embedder, reranker),
                                   cache_dir=self.cache_dir)
        try:
            await self.retriever.build()
        except Exception as exc:  # pgvector down or misconfigured: keep answering from memory
            if isinstance(store, MemoryStore):
                raise
            log.warning("pgvector unavailable (%s); docs index falling back to in-memory search", exc)
            self.retriever.store = MemoryStore()
            await self.retriever.build()
        log.info("docs index built: %d chunks from %d documents (%d duplicates dropped), store=%s, embedder=%s, "
                 "mode=%s", len(corpus.chunks), len(corpus.docs), len(corpus.dropped), self.retriever.store.name,
                 self.embedder.name, mode)

    def _make_reranker(self, corpus: rag_corpus.Corpus) -> Reranker | None:
        spec = self._reranker_spec
        if spec is None or spec == "none":
            return None
        if spec == "lexical":
            from ..rag.bm25 import BM25

            return LexicalReranker(BM25([c.index_text for c in corpus.chunks]).idf)
        if spec == "cross-encoder":
            return CrossEncoderReranker(cache_dir=self._model_cache_dir)
        return spec  # type: ignore[return-value]

    async def search(self, query: str, k: int = 3) -> DocsOutput:
        if self.retriever is None:
            await self.build()
        hits = await self.retriever.search(query, k)  # type: ignore[union-attr]
        chunks = [_sanitize(h.chunk.as_dict(), h.score) for h in hits]
        return DocsOutput(query=query, chunks=chunks)


def _sanitize(chunk: dict, score: float) -> DocChunk:
    hit = detect_injection(chunk["text"])
    if hit:
        log.warning("redacted doc chunk %s (matched %r)", chunk["id"], hit)
        return DocChunk(id=chunk["id"], title=chunk["title"], url=chunk["url"], section=chunk.get("section", ""),
                        text="[passage withheld: it contained instructions rather than reference text]",
                        score=round(score, 3), redacted=True)
    return DocChunk(id=chunk["id"], title=chunk["title"], url=chunk["url"], text=chunk["text"],
                    section=chunk.get("section", ""), score=round(score, 3))
