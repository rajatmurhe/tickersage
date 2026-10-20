"""Load the docs corpus, split it into chunks along its headings, and drop duplicate chunks.

Two kinds of files: the hand-written glossary (docs_corpus/*.md) and the fetched sources
(docs_corpus/sources/*.md, written by app.rag.ingest). Both are Markdown with front matter giving
the title, source URL and licence.

Chunks follow the document's structure: a chunk never spans two sections unless the first one is too
short to stand alone, paragraphs are kept whole where they fit, and a section that has to be split
repeats the last sentence of the previous chunk so a definition isn't cut off from its context. Each
chunk keeps its heading path ("Ethereum Whitepaper > Ethereum > Messages"), which goes into the
indexed text but not the quoted text.

Chunk ids are `<doc id>#<n>`, numbered before dedup so dropping a duplicate never renumbers the rest.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

TARGET_WORDS = 160
MAX_WORDS = 240
MIN_WORDS = 40
OVERLAP_WORDS = 40  # carry at most this much of the previous chunk into the next one in the same section
NEAR_DUP = 0.85  # Jaccard over word 5-gram shingles


@dataclass
class Chunk:
    id: str
    doc_id: str
    title: str
    url: str
    license: str
    section: str
    text: str
    position: int

    @property
    def index_text(self) -> str:
        """What BM25 and the embedder see: title and heading path give a chunk its context."""
        head = self.title if not self.section else f"{self.title} | {self.section}"
        return f"{head}\n{self.text}"

    def as_dict(self) -> dict:
        return {"id": self.id, "doc_id": self.doc_id, "title": self.title, "url": self.url,
                "license": self.license, "section": self.section, "text": self.text}


@dataclass
class Corpus:
    chunks: list[Chunk]
    docs: dict[str, dict]
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (dropped id, kept id it duplicated)

    @property
    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for c in self.chunks:
            h.update(c.id.encode())
            h.update(c.index_text.encode())
        return h.hexdigest()[:16]


def front_matter(raw: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        return {}, raw
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, m.group(2)


def load(docs_dir: Path) -> Corpus:
    paths = sorted(docs_dir.glob("*.md")) + sorted((docs_dir / "sources").glob("*.md"))
    chunks: list[Chunk] = []
    docs: dict[str, dict] = {}
    for path in paths:
        if path.name.upper() == "README.MD":
            continue
        meta, body = front_matter(path.read_text(encoding="utf-8"))
        doc_id = path.stem
        doc = {"id": doc_id, "title": meta.get("title", doc_id), "url": meta.get("url", ""),
               "license": meta.get("license", "Original TickerSage text"),
               "kind": "source" if path.parent.name == "sources" else "glossary"}
        docs[doc_id] = doc
        for n, (section, text) in enumerate(chunk_markdown(body)):
            chunks.append(Chunk(id=f"{doc_id}#{n}", doc_id=doc_id, title=doc["title"], url=doc["url"],
                                license=doc["license"], section=section, text=text, position=n))
    kept, dropped = dedup(chunks)
    return Corpus(kept, docs, dropped)


# ------------------------------------------------------------------ chunking


def _sections(body: str) -> list[tuple[str, list[str]]]:
    """[(heading path, paragraphs)] in reading order. Fenced code stays one paragraph."""
    sections: list[tuple[str, list[str]]] = []
    path: list[tuple[int, str]] = []
    paras: list[str] = []
    buf: list[str] = []
    in_code = False

    def end_para():
        text = "\n".join(buf).strip()
        if text:
            paras.append(text)
        buf.clear()

    def end_section():
        end_para()
        if paras:
            sections.append((" > ".join(t for _, t in path), paras.copy()))
        paras.clear()

    for line in body.splitlines():
        if line.strip().startswith("```"):
            buf.append(line)
            in_code = not in_code
            if not in_code:
                end_para()
            continue
        if in_code:
            buf.append(line)
            continue
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if m:
            end_section()
            depth = len(m.group(1))
            path = [p for p in path if p[0] < depth] + [(depth, re.sub(r"[*_`]", "", m.group(2)))]
            continue
        if not line.strip():
            end_para()
        else:
            buf.append(line)
    end_section()
    return sections


def _clean(text: str) -> str:
    if text.lstrip().startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        return "Code: " + " ".join(" ".join(lines).split())
    return " ".join(text.split())


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`\"(])", text) if s]


def _split_long(text: str) -> list[str]:
    """Split a paragraph longer than MAX_WORDS on sentence boundaries (hard cut as a last resort)."""
    pieces, cur = [], []
    for sent in _sentences(text):
        words = sent.split()
        while len(words) > MAX_WORDS:
            pieces.append(" ".join(words[:MAX_WORDS]))
            words = words[MAX_WORDS:]
        if cur and len(cur) + len(words) > TARGET_WORDS:
            pieces.append(" ".join(cur))
            cur = []
        cur.extend(words)
    if cur:
        pieces.append(" ".join(cur))
    return pieces


def chunk_markdown(body: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    carry_section, carry = "", []  # a short section waiting to join the next one
    for section, paras in _sections(body):
        units: list[str] = []
        for p in paras:
            p = _clean(p)
            units.extend(_split_long(p) if len(p.split()) > MAX_WORDS else [p])
        cur: list[str] = list(carry)
        if carry and section:
            # Joining a short section to this one: keep this one's heading in the text so it isn't lost.
            cur.extend(f"{section.rsplit(' > ', 1)[-1]}:".split())
        sec_label = carry_section or section
        carry, carry_section = [], ""
        for unit in units:
            words = unit.split()
            if cur and len(cur) + len(words) > TARGET_WORDS and len(cur) >= MIN_WORDS:
                out.append((sec_label, " ".join(cur)))
                tail = _sentences(" ".join(cur))[-1].split()
                cur = tail if len(tail) <= OVERLAP_WORDS and len(tail) + len(words) <= MAX_WORDS else []
                sec_label = section
            cur.extend(words)
        if len(cur) < MIN_WORDS:
            carry, carry_section = cur, sec_label  # too short to stand alone: join the next section
        elif cur:
            out.append((sec_label, " ".join(cur)))
    if carry:
        if out and len(out[-1][1].split()) + len(carry) <= MAX_WORDS:
            out[-1] = (out[-1][0], out[-1][1] + " " + " ".join(carry))
        else:
            out.append((carry_section, " ".join(carry)))
    return out


# ------------------------------------------------------------------ dedup


def _norm_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def dedup(chunks: list[Chunk]) -> tuple[list[Chunk], list[tuple[str, str]]]:
    """Drop exact duplicates (same normalised text) and near duplicates (shingle Jaccard >= NEAR_DUP).

    The first occurrence wins: glossary files load first, then sources in id order.
    """
    seen_exact: dict[str, str] = {}
    shingles_of: dict[str, set[int]] = {}
    postings: dict[int, list[str]] = {}
    kept, dropped = [], []
    for c in chunks:
        toks = _norm_tokens(c.text)
        key = hashlib.sha1(" ".join(toks).encode()).hexdigest()
        if key in seen_exact:
            dropped.append((c.id, seen_exact[key]))
            continue
        sh = {hash(" ".join(toks[i:i + 5])) for i in range(max(1, len(toks) - 4))}
        overlap: dict[str, int] = {}
        for s in sh:
            for other in postings.get(s, ()):
                overlap[other] = overlap.get(other, 0) + 1
        dup_of = next((o for o, n in overlap.items()
                       if n / (len(sh) + len(shingles_of[o]) - n) >= NEAR_DUP), None)
        if dup_of:
            dropped.append((c.id, dup_of))
            continue
        seen_exact[key] = c.id
        shingles_of[c.id] = sh
        for s in sh:
            postings.setdefault(s, []).append(c.id)
        kept.append(c)
    return kept, dropped
