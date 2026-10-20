"""Resolve the eval cases' gold (document + phrase) to chunk ids in the current corpus."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .. import _BACKEND  # noqa: F401  (puts backend/ on sys.path)
from app.rag.corpus import Chunk


def norm(text: str) -> str:
    return " ".join(re.sub(r"[*`]", "", text).lower().split())


def load_cases(path: Path, chunks: list[Chunk]) -> list[dict]:
    """Cases with a `gold_ids` set. Raises if a gold phrase matches no chunk (the corpus or chunker moved)."""
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    cases = yaml.safe_load(path.read_text())["cases"]
    missing = []
    for case in cases:
        ids: list[str] = []
        for g in case["gold"]:
            phrase = norm(g["contains"])
            hits = [c.id for c in by_doc.get(g["doc"], []) if phrase in norm(f"{c.section} {c.text}")]
            if not hits:
                missing.append(f"{case['id']}: {g['doc']} / {g['contains']!r}")
            ids.extend(h for h in hits if h not in ids)
        case["gold_ids"] = ids
    if missing:
        raise ValueError("gold phrases with no matching chunk:\n  " + "\n  ".join(missing))
    return cases
