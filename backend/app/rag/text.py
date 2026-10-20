"""Index terms for BM25 and the lexical reranker: the shared tokenizer plus a light suffix stemmer.

The hash embedder and the semantic cache keep using `tokens` unchanged; stemming here only affects docs search.
"""

from __future__ import annotations

from functools import lru_cache

from ..embeddings import tokens

# No "-ation": it would merge liquidation with liquidity.
_SUFFIXES = ("ments", "ment", "ness", "ings", "ing", "ied", "ies", "ers", "ed",
             "er", "es", "ly", "e", "s")


@lru_cache(maxsize=50_000)
def stem(word: str) -> str:
    if len(word) <= 4 or not word.isalpha():
        return word
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[: -len(suf)]
    return word


def terms(text: str) -> list[str]:
    return [stem(t) for t in tokens(text)]
