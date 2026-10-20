"""Second-stage rerankers: score (query, chunk) pairs for the top fused candidates.

`CrossEncoderReranker` runs a MS MARCO MiniLM cross-encoder on CPU through fastembed (ONNX, ~80 MB download
once). `LexicalReranker` needs no model: it scores how much of the query a chunk covers, which is the
offline fallback and what CI runs. Both return scores in [0, 1] so one abstain threshold idea works for each.
"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Protocol

from .corpus import Chunk
from .text import terms as tokens


class Reranker(Protocol):
    name: str
    # Final score = blend * reranker score + (1 - blend) * normalised fusion score.
    blend: float

    async def score(self, query: str, chunks: list[Chunk]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(self, model: str = "Xenova/ms-marco-MiniLM-L-6-v2", cache_dir: str | None = None) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model, cache_dir=cache_dir)
        self.name = model
        self.blend = 1.0

    def _score_sync(self, query: str, texts: list[str]) -> list[float]:
        return [1 / (1 + math.exp(-x)) for x in self._model.rerank(query, texts, batch_size=32)]

    async def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        if not chunks:
            return []
        return await asyncio.to_thread(self._score_sync, query, [c.index_text for c in chunks])


class LexicalReranker:
    """Query coverage: the IDF-weighted share of the query's terms (and adjacent term pairs) found in the chunk,
    with the heading path counted as part of it. Rewards chunks that answer the whole question over ones that
    repeat one of its words many times, which is BM25's failure mode on long pages.

    Two priors for "what is X?" questions: a chunk whose document title is X (ignoring a "(Wikipedia)"-style
    suffix) gets a bonus, and so does a document's opening chunk, which is where definitions sit.
    """

    name = "lexical-coverage"
    TITLE_BONUS, CONCEPT_BONUS, LEAD_BONUS = 0.10, 0.15, 0.15

    def __init__(self, idf: dict[str, float], blend: float = 0.5) -> None:
        self.blend = blend
        self.idf = idf
        self.default_idf = max(idf.values()) if idf else 1.0

    async def score(self, query: str, chunks: list[Chunk]) -> list[float]:
        q = tokens(query)
        if not q:
            return [0.0] * len(chunks)
        terms = set(q)
        pairs = {(a, b) for a, b in zip(q, q[1:], strict=False)}
        weight = {t: self.idf.get(t, self.default_idf) for t in terms}
        total = sum(weight.values()) + 0.5 * len(pairs)
        scale = 1 + self.TITLE_BONUS + self.CONCEPT_BONUS + self.LEAD_BONUS
        out = []
        for c in chunks:
            ct = tokens(c.index_text)
            have = set(ct)
            have_pairs = set(zip(ct, ct[1:], strict=False))
            got = sum(w for t, w in weight.items() if t in have) + 0.5 * len(pairs & have_pairs)
            head = set(tokens(f"{c.title} {c.section}"))
            score = got / total + self.TITLE_BONUS * len(terms & head) / len(terms)
            concept = set(tokens(re.sub(r"\s*\([^)]*\)\s*$", "", c.title)))
            if concept and concept <= terms:
                score += self.CONCEPT_BONUS
            if c.position == 0:
                score += self.LEAD_BONUS
            out.append(score / scale)
        return out
