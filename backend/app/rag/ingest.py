"""Fetch the sources in docs_corpus/manifest.yaml and write them as Markdown under docs_corpus/sources/.

    cd backend && python -m app.rag.ingest            # fetch everything
    cd backend && python -m app.rag.ingest --only eip-1559 wiki-bitcoin

Each output file gets front matter with the title, canonical URL, licence, fetch time and a SHA-256 of the
body, and sources.lock.json records the same for the whole corpus. The fetched text is committed, so the app,
tests and CI never need the network. Fetching needs `pypdf` for the Bitcoin whitepaper.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

import httpx
import yaml

CORPUS_DIR = Path(__file__).resolve().parents[2] / "docs_corpus"
MANIFEST = CORPUS_DIR / "manifest.yaml"
SOURCES_DIR = CORPUS_DIR / "sources"
LOCK = CORPUS_DIR / "sources.lock.json"
UA = "TickerSage/0.1 docs ingest (https://github.com/rajatmurhe/tickersage)"

# Wikipedia sections that are lists of links or citations, not reference text.
WIKI_DROP = {"see also", "references", "external links", "notes", "further reading", "bibliography", "sources",
             "citations", "footnotes", "explanatory notes"}


def fetch_source(src: dict, http: httpx.Client) -> tuple[str, dict]:
    fmt = src["format"]
    extra: dict = {}
    if fmt == "wikipedia":
        page = unquote(src["url"].rsplit("/wiki/", 1)[1])
        r = http.get("https://en.wikipedia.org/w/api.php", params={
            "action": "query", "prop": "extracts|revisions", "explaintext": 1, "exsectionformat": "wiki",
            "rvprop": "ids", "titles": page, "format": "json", "formatversion": 2, "redirects": 1})
        r.raise_for_status()
        pg = r.json()["query"]["pages"][0]
        extra["revision"] = pg["revisions"][0]["revid"]
        return wiki_to_md(pg["extract"]), extra
    r = http.get(src["fetch"])
    r.raise_for_status()
    if fmt == "pdf":
        from pypdf import PdfReader

        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
        return pdf_to_md(text), extra
    if fmt == "mediawiki":
        body, lic = mediawiki_to_md(r.text)
        extra["license_detail"] = lic
        return body, extra
    return markdown_clean(r.text), extra


# ------------------------------------------------------------------ converters


def wiki_to_md(text: str) -> str:
    out, drop = [], False
    for line in text.splitlines():
        m = re.match(r"^(=+)\s*(.*?)\s*=+\s*$", line)
        if m:
            depth = len(m.group(1))
            if depth == 2:
                drop = m.group(2).lower() in WIKI_DROP
            if not drop:
                out.append(f"\n{'#' * depth} {m.group(2)}\n")
            continue
        if not drop:
            out.append(line)
    return _tidy("\n".join(out))


def pdf_to_md(text: str) -> str:
    # The whitepaper's numbered section titles ("4. Proof-of-Work") sit on their own lines; make them headings.
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^\d{1,2}\.\s+[A-Z][A-Za-z \-]{2,40}$", s):
            lines.append(f"\n## {s}\n")
        else:
            lines.append(s)
    text = "\n".join(lines)
    text = re.sub(r"-\n(?=[a-z])", "", text)  # re-join hyphenated line breaks
    text = re.sub(r"(?<![\n.:])\n(?!\n|#)", " ", text)  # PDF lines -> paragraphs
    return _tidy(text)


def mediawiki_to_md(text: str) -> tuple[str, str]:
    lic = ""
    m = re.search(r"^\s*License:\s*(.+)$", text, re.M)
    if m:
        lic = m.group(1).strip()
    text = re.sub(r"<pre>\s*\n\s*BIP:.*?</pre>", "", text, count=1, flags=re.S)  # header block
    text = re.sub(r"\{\|.*?\|\}", _mw_table, text, flags=re.S)
    text = re.sub(r"^(=+)\s*(.*?)\s*=+\s*$", lambda m: f"\n{'#' * len(m.group(1))} {m.group(2)}\n", text, flags=re.M)
    text = re.sub(r"<(source|syntaxhighlight)[^>]*>(.*?)</\1>", lambda m: f"\n```\n{m.group(2).strip()}\n```\n",
                  text, flags=re.S)
    text = re.sub(r"<pre>(.*?)</pre>", lambda m: f"\n```\n{m.group(1).strip()}\n```\n", text, flags=re.S)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[(https?://\S+)\s+([^\]]+)\]", r"\2", text)
    text = re.sub(r"'''?", "", text)
    text = re.sub(r"<sub>(.*?)</sub>", r"_\1", text)
    text = re.sub(r"<sup>(.*?)</sup>", r"^\1", text)
    text = re.sub(r"</?(code|tt|b|i|br|ref|small|span|div)[^>]*>", "", text)
    text = re.sub(r"^\*+\s*", "- ", text, flags=re.M)
    text = re.sub(r"^#+(?=[^#\s])", "1. ", text, flags=re.M)  # numbered list items (not headings)
    return _tidy(text), lic


def _mw_table(m: re.Match) -> str:
    rows = []
    for row in re.split(r"\n\|-", m.group(0)):
        cells = [c.strip() for c in re.split(r"\n[|!]|\|\||!!", row) if c.strip() and not c.strip().startswith("{|")]
        cells = [c for c in cells if c != "|}"]
        if cells:
            rows.append(" | ".join(cells))
    return "\n" + "\n".join(rows) + "\n"


def markdown_clean(text: str) -> str:
    text = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)  # front matter
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"^\s*<[A-Z][A-Za-z]*[^>]*/>\s*$", "", text, flags=re.M)  # self-closing MDX components
    text = re.sub(r"^\s*</?[A-Z][A-Za-z]*[^>]*>\s*$", "", text, flags=re.M)  # MDX wrappers (<ExpandableCard>)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)  # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links -> their text
    text = re.sub(r"\s*\{#[\w-]+\}", "", text)  # heading anchors
    text = re.sub(r"</?(br|div|span|sup|sub|details|summary|a|img|p)[^>]*>", "", text)
    text = re.sub(r"^\s*\|?\s*:?-{3,}.*$", "", text, flags=re.M)  # table separator rows
    return _tidy(text)


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


# ------------------------------------------------------------------ main


def write_source(src: dict, body: str, extra: dict, licenses: dict) -> dict:
    sha = hashlib.sha256(body.encode()).hexdigest()
    lic = licenses[src["license"]]
    if extra.get("license_detail"):
        lic = f"{extra['license_detail']} (from the BIP header)"
    meta = {"title": src["title"], "url": src["url"], "license": lic, "format": src["format"],
            "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "sha256": sha}
    if "revision" in extra:
        meta["revision"] = extra["revision"]
    head = "".join(f"{k}: {v}\n" for k, v in meta.items())
    (SOURCES_DIR / f"{src['id']}.md").write_text(f"---\n{head}---\n{body}", encoding="utf-8")
    return {"id": src["id"], **meta, "words": len(body.split())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", nargs="*", help="source ids to (re)fetch")
    args = ap.parse_args(argv)
    manifest = yaml.safe_load(MANIFEST.read_text())
    SOURCES_DIR.mkdir(exist_ok=True)
    lock = {e["id"]: e for e in json.loads(LOCK.read_text())} if LOCK.exists() else {}
    failed = []
    with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as http:
        for src in manifest["sources"]:
            if args.only and src["id"] not in args.only:
                continue
            try:
                body, extra = fetch_source(src, http)
            except Exception as exc:  # keep going; report at the end
                failed.append(f"{src['id']}: {exc}")
                continue
            lock[src["id"]] = write_source(src, body, extra, manifest["licenses"])
            print(f"{src['id']:32} {lock[src['id']]['words']:>7} words")
    ids = [s["id"] for s in manifest["sources"]]
    LOCK.write_text(json.dumps([lock[i] for i in ids if i in lock], indent=1) + "\n")
    for f in failed:
        print("FAILED", f, file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
