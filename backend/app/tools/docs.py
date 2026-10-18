"""Retrieval over a small corpus of crypto glossary and exchange API notes.

Each corpus file is Markdown with a front-matter block giving its title and source
URL. Chunks come back with that URL so every concept answer can cite where it came
from. Backends: in-memory cosine search (default) or PostgreSQL + pgvector.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from ..embeddings import Embedder, tokens
from ..guardrails import detect_injection

log = logging.getLogger(__name__)

MIN_SCORE = 0.12
CHUNK_WORDS = 110


class DocsInput(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    k: int = Field(default=3, ge=1, le=5)


class DocChunk(BaseModel):
    id: str
    title: str
    url: str
    text: str
    score: float
    redacted: bool = False


class DocsOutput(BaseModel):
    query: str
    chunks: list[DocChunk]
    source: str = "TickerSage docs corpus"


def load_corpus(docs_dir: Path) -> list[dict]:
    chunks: list[dict] = []
    for path in sorted(docs_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _front_matter(raw)
        title, url = meta.get("title", path.stem), meta.get("url", "")
        for i, text in enumerate(_chunk(body)):
            chunks.append({"id": f"{path.stem}#{i}", "title": title, "url": url, "text": text})
    return chunks


def _front_matter(raw: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        return {}, raw
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, m.group(2)


def _chunk(body: str) -> list[str]:
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks, current = [], []
    for para in paras:
        words = para.split()
        if current and len(current) + len(words) > CHUNK_WORDS:
            chunks.append(" ".join(current))
            current = []
        current.extend(words)
    if current:
        chunks.append(" ".join(current))
    return chunks


class DocsIndex:
    def __init__(self, docs_dir: Path, embedder: Embedder, backend: str = "memory", pg_url: str | None = None):
        self.docs_dir = docs_dir
        self.embedder = embedder
        self.backend = backend
        self.pg_url = pg_url
        self.chunks: list[dict] = []
        self.matrix: np.ndarray | None = None

    async def build(self) -> None:
        self.chunks = load_corpus(self.docs_dir)
        texts = [f"{c['title']}. {c['title']}. {c['text']}" for c in self.chunks]
        self.matrix = await self.embedder.embed(texts)
        if self.backend == "pgvector" and self.pg_url:
            await self._pg_upsert()
        log.info("docs index built: %d chunks (%s)", len(self.chunks), self.backend)

    async def search(self, query: str, k: int = 3) -> DocsOutput:
        if self.matrix is None:
            await self.build()
        qvec = (await self.embedder.embed([query]))[0]
        if self.backend == "pgvector" and self.pg_url:
            scored = await self._pg_search(qvec, k * 3)
        else:
            sims = self.matrix @ qvec  # type: ignore[operator]
            scored = [(float(sims[i]), self.chunks[i]) for i in range(len(self.chunks))]
        qtoks = set(tokens(query))
        rescored = []
        for score, chunk in scored:
            title_toks = set(tokens(chunk["title"]))
            if title_toks and title_toks <= qtoks:
                score += 0.15  # the query names the concept exactly
            if chunk["id"].endswith("#0"):
                score += 0.03  # the opening chunk holds the definition
            rescored.append((score, chunk))
        rescored.sort(key=lambda x: x[0], reverse=True)
        out, seen_titles = [], set()
        for score, chunk in rescored:
            if score < MIN_SCORE or len(out) >= k:
                break
            if chunk["title"] in seen_titles and len(out) >= 1 and score < rescored[0][0] - 0.1:
                continue
            seen_titles.add(chunk["title"])
            out.append(_sanitize(chunk, score))
        # Keep each document's chunks together and in reading order, best document first.
        best: dict[str, float] = {}
        for c in out:
            best[c.title] = max(best.get(c.title, 0.0), c.score)
        out.sort(key=lambda c: (-best[c.title], int(c.id.rsplit("#", 1)[-1])))
        return DocsOutput(query=query, chunks=out)

    # ------------------------------------------------------------ pgvector

    async def _pg_upsert(self) -> None:  # pragma: no cover - needs a running Postgres
        import psycopg

        async with await psycopg.AsyncConnection.connect(self.pg_url) as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS doc_chunks (id text PRIMARY KEY, title text, url text, "
                f"body text, embedding vector({self.embedder.dim}))"
            )
            async with conn.cursor() as cur:
                for chunk, vec in zip(self.chunks, self.matrix, strict=True):  # type: ignore[arg-type]
                    await cur.execute(
                        "INSERT INTO doc_chunks (id, title, url, body, embedding) VALUES (%s,%s,%s,%s,%s::vector) "
                        "ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, url=EXCLUDED.url, "
                        "body=EXCLUDED.body, embedding=EXCLUDED.embedding",
                        (chunk["id"], chunk["title"], chunk["url"], chunk["text"], _vec_literal(vec)),
                    )
            await conn.commit()

    async def _pg_search(self, qvec: np.ndarray, k: int) -> list[tuple[float, dict]]:  # pragma: no cover
        import psycopg

        async with await psycopg.AsyncConnection.connect(self.pg_url) as conn:
            cur = await conn.execute(
                "SELECT id, title, url, body, 1 - (embedding <=> %s::vector) AS score FROM doc_chunks "
                "ORDER BY embedding <=> %s::vector LIMIT %s",
                (_vec_literal(qvec), _vec_literal(qvec), k),
            )
            rows = await cur.fetchall()
        return [(float(r[4]), {"id": r[0], "title": r[1], "url": r[2], "text": r[3]}) for r in rows]


def _sanitize(chunk: dict, score: float) -> DocChunk:
    hit = detect_injection(chunk["text"])
    if hit:
        log.warning("redacted doc chunk %s (matched %r)", chunk["id"], hit)
        return DocChunk(id=chunk["id"], title=chunk["title"], url=chunk["url"],
                        text="[passage withheld: it contained instructions rather than reference text]",
                        score=round(score, 3), redacted=True)
    return DocChunk(id=chunk["id"], title=chunk["title"], url=chunk["url"], text=chunk["text"], score=round(score, 3))


def _vec_literal(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
