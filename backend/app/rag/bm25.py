"""Okapi BM25 over the shared tokenizer (crypto synonyms, stopwords) plus light stemming (app/rag/text.py)."""

from __future__ import annotations

import math
from collections import Counter

from .text import terms as tokens


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.lengths: list[int] = []
        for i, text in enumerate(docs):
            counts = Counter(tokens(text))
            self.lengths.append(sum(counts.values()))
            for term, tf in counts.items():
                self.postings.setdefault(term, []).append((i, tf))
        self.n = len(docs)
        self.avg_len = (sum(self.lengths) / self.n) if self.n else 0.0
        self.idf = {t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        scores: dict[int, float] = {}
        for term in set(tokens(query)):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, tf in self.postings[term]:
                norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg_len)
                scores[i] = scores.get(i, 0.0) + idf * tf * (self.k1 + 1) / (tf + norm)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
