"""Retrieval evals for search_docs: recall@k, MRR, no-answer handling, and answer-level citation metrics.

    python -m evals.rag.run                                   # offline ablation on the dev split (what CI runs)
    python -m evals.rag.run --cases evals/rag/cases_holdout.yaml --out ... --report ...
    python -m evals.rag.run --configs bm25 vector:bge hybrid:bge hybrid_rerank:bge:ce   # local models
    python -m evals.rag.run --answers live --configs hybrid_rerank:bge:ce               # LLM answers + judge
    python -m evals.rag.run --sweep hybrid_rerank:hash:lexical   # dev only: no-answer threshold sweep

A config is mode[:embedder[:reranker]]: mode in bm25 | vector | hybrid | hybrid_rerank, embedder hash (offline
lexical hashing, the CI path) or bge (BAAI/bge-small-en-v1.5 via fastembed), reranker lexical or ce
(ms-marco MiniLM cross-encoder via fastembed).

Metrics (answerable questions unless noted):
  recall@k  share of questions with at least one gold chunk in the top k of the ranking (before abstaining)
  MRR       mean reciprocal rank of the first gold chunk in the top 10 (0 when none)
  no-answer share of off-corpus questions (gold: []) where the retriever returned nothing
  false abstain  share of answerable questions where it returned nothing
Answer metrics (k=3 chunks, as the agent uses; on every question):
  citation accuracy   share of answerable questions whose answer cites at least one gold chunk
  bad citations       share of answers with a [n] marker that maps to no returned chunk (non-existent source)
  faithfulness        share of answer sentences whose content words are >= 70% found in the chunks they cite
                      (lexical; template answers copy chunk text, so this matters for LLM answers)
  judge grounded      LLM judge's "grounded" verdict rate (live answers only)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .. import _BACKEND  # noqa: F401  (puts backend/ on sys.path)
from app.config import Settings
from app.embeddings import Embedder, HashingEmbedder, tokens
from app.rag import corpus as rag_corpus
from app.rag.bm25 import BM25
from app.rag.rerank import CrossEncoderReranker, LexicalReranker
from app.rag.retriever import Retriever
from app.schemas import RouteDecision, ToolResult
from app.tools.docs import DocsOutput, _sanitize, default_thresholds
from app.templates import build_citations, docs_block

from .gold import load_cases

RAG_DIR = Path(__file__).resolve().parent
DEV = RAG_DIR / "cases_dev.yaml"
OFFLINE_CONFIGS = ["bm25", "vector:hash", "hybrid:hash", "hybrid_rerank:hash:lexical"]
SHIPPED_OFFLINE = "hybrid_rerank:hash:lexical"
# CI gate on the shipped offline config, set just under the lower of its dev and held-out first-run numbers
# (dev: R@5 81.0%, MRR 0.632, no-answer 3/4, citation 70.7%; held-out: 80.0%, 0.630, 2/3, 70.0%), so a
# regression fails the build. Bad citations must stay at zero.
GATES = {"recall@5": 0.78, "mrr": 0.60, "no_answer": 0.66, "citation_accuracy": 0.68, "bad_citations": 0.0}

_models: dict[str, object] = {}


def _embedder(name: str, s: Settings) -> Embedder:
    if name == "hash":
        return HashingEmbedder()
    if name == "bge":
        if "bge" not in _models:
            from app.embeddings import LocalEmbedder

            _models["bge"] = LocalEmbedder(s.embedding_model, cache_dir=str(s.model_cache_dir))
        return _models["bge"]  # type: ignore[return-value]
    raise SystemExit(f"unknown embedder {name!r}")


def make_retriever(spec: str, corpus: rag_corpus.Corpus, s: Settings) -> Retriever:
    parts = spec.split(":")
    mode, emb, rr = parts[0], (parts[1] if len(parts) > 1 else "hash"), (parts[2] if len(parts) > 2 else None)
    embedder = _embedder(emb, s)
    reranker = None
    if rr == "lexical":
        reranker = LexicalReranker(BM25([c.index_text for c in corpus.chunks]).idf)
    elif rr == "ce":
        if "ce" not in _models:
            _models["ce"] = CrossEncoderReranker(s.rerank_model, cache_dir=str(s.model_cache_dir))
        reranker = _models["ce"]
    return Retriever(corpus, embedder, reranker, mode=mode, thresholds=default_thresholds(embedder, reranker),
                     cache_dir=s.rag_cache_dir)


def label(spec: str) -> str:
    parts = spec.split(":")
    mode = {"bm25": "BM25 only", "vector": "vector only", "hybrid": "hybrid (RRF)",
            "hybrid_rerank": "hybrid + rerank"}[parts[0]]
    if parts[0] == "bm25":
        return mode
    emb = {"hash": "hash embedder", "bge": "bge-small"}[parts[1] if len(parts) > 1 else "hash"]
    rr = {"lexical": "lexical rerank", "ce": "MiniLM cross-encoder"}.get(parts[2]) if len(parts) > 2 else None
    return f"{mode}, {emb}" + (f", {rr}" if rr else "")


# ------------------------------------------------------------------ answers

_CITE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def cited_numbers(answer: str) -> list[int]:
    return [int(n) for m in _CITE.finditer(answer) for n in m.group(1).split(",")]


def lexical_faithfulness(answer: str, by_n: dict[int, str]) -> tuple[int, int]:
    """(supported sentences, content sentences): a sentence is supported when >= 70% of its content words
    appear in the chunk(s) it cites, or in the previous cited sentence's chunks when it carries no marker."""
    supported = total = 0
    last: list[int] = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", answer):
        ns = [n for n in cited_numbers(sent) if n in by_n] or last
        words = set(tokens(_CITE.sub("", sent)))
        if len(words) < 3:
            continue
        total += 1
        if ns:
            last = ns
            have = set(tokens(" ".join(by_n[n] for n in ns)))
            if len(words & have) / len(words) >= 0.7:
                supported += 1
    return supported, total


async def answer_case(case: dict, out: DocsOutput, llm=None) -> dict:
    result = ToolResult(id=1, tool="search_docs", args={"query": case["q"], "k": 3}, ok=True,
                        data=out.model_dump(mode="json"), source=out.source)
    citations, _, by_chunk = build_citations([result])
    judged = None
    if llm is None:
        answer = docs_block(result.data, by_chunk)
    else:
        from app.synthesizer import synthesize

        from ..judge import judge

        from app.llm import LLMUnavailable

        route = RouteDecision(intent="concept", topic=case["q"])
        for attempt in range(5):  # free tiers answer 429 under load; back off rather than score a fallback
            try:
                resp = await synthesize(llm, case["q"], route, [result], None)
                break
            except LLMUnavailable as exc:
                if attempt == 4:
                    return {"llm_error": str(exc)[:300]}
                await asyncio.sleep(20 * (attempt + 1))
        answer = resp.text
        judged = await judge(llm, case["q"], answer, [result], max_tokens=12000)  # reasoning models think at length
    n_to_id = {n: cid for cid, n in by_chunk.items()}
    text_of = {by_chunk[c.id]: c.text for c in out.chunks if c.id in by_chunk}
    # Brackets inside quoted chunk text (code like `x = [0]`) are the source's, not citation markers.
    markers_only = answer
    for c in out.chunks:
        markers_only = markers_only.replace(c.text, " ")
    nums = cited_numbers(markers_only)
    sup, tot = lexical_faithfulness(answer, text_of)
    return {
        "answer": answer,
        "cited": sorted({n_to_id[n] for n in nums if n in n_to_id}),
        "bad_citation": any(n not in n_to_id for n in nums),
        "faithful_sentences": sup, "content_sentences": tot,
        "judge": judged,
    }


# ------------------------------------------------------------------ run


async def run_config(spec: str, cases: list[dict], corpus: rag_corpus.Corpus, s: Settings,
                     answers: str | None, llm=None, concurrency: int = 1) -> dict:
    retriever = make_retriever(spec, corpus, s)
    t0 = time.perf_counter()
    await retriever.build()
    build_s = time.perf_counter() - t0
    rows, lat, outs = [], [], []
    for case in cases:  # retrieval runs one query at a time so the latency numbers aren't skewed
        t = time.perf_counter()
        ranked = await retriever.rank(case["q"], 10)
        lat.append((time.perf_counter() - t) * 1000)
        ids = [h.chunk.id for h in ranked]
        gold = set(case["gold_ids"])
        first = next((i for i, cid in enumerate(ids) if cid in gold), None)
        top = await retriever.search(case["q"], 3)
        rows.append({"id": case["id"], "q": case["q"], "gold": case["gold_ids"], "top": ids[:5], "first_gold_rank":
                     None if first is None else first + 1, "returned": [h.chunk.id for h in top],
                     "top_signals": {k: round(v, 4) for k, v in ranked[0].signals.items()} if ranked else {}})
        outs.append(DocsOutput(query=case["q"], chunks=[_sanitize(h.chunk.as_dict(), h.score) for h in top]))
    if answers:
        gate = asyncio.Semaphore(concurrency)

        async def answer(i: int) -> None:
            async with gate:
                rows[i].update(await answer_case(cases[i], outs[i], llm if answers == "live" else None))
            if answers == "live":
                print(f"  {spec} {cases[i]['id']} answered", file=sys.stderr, flush=True)

        await asyncio.gather(*(answer(i) for i in range(len(cases))))
    return {"config": spec, "label": label(spec), "build_s": round(build_s, 2), "metrics": metrics(rows, answers),
            "p50_ms": round(statistics.median(lat), 2), "p95_ms": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 2),
            "cases": rows}


def metrics(rows: list[dict], answers: str | None) -> dict:
    ans = [r for r in rows if r["gold"]]
    none = [r for r in rows if not r["gold"]]
    m: dict = {"n_answerable": len(ans), "n_no_answer": len(none)}
    for k in (1, 3, 5, 10):
        m[f"recall@{k}"] = _ratio(sum(1 for r in ans if r["first_gold_rank"] and r["first_gold_rank"] <= k), len(ans))
    m["mrr"] = round(sum(1 / r["first_gold_rank"] for r in ans if r["first_gold_rank"]) / max(1, len(ans)), 4)
    m["no_answer"] = _ratio(sum(1 for r in none if not r["returned"]), len(none))
    m["false_abstain"] = _ratio(sum(1 for r in ans if not r["returned"]), len(ans))
    if answers:
        m["llm_errors"] = sum(1 for r in rows if "llm_error" in r)  # no answer after retries: left out below
        rows = [r for r in rows if "answer" in r]
        ans = [r for r in rows if r["gold"]]
        m["citation_accuracy"] = _ratio(sum(1 for r in ans if set(r["cited"]) & set(r["gold"])), len(ans))
        m["bad_citations"] = _ratio(sum(1 for r in rows if r["bad_citation"]), len(rows))
        m["faithfulness"] = _ratio(sum(r["faithful_sentences"] for r in rows), sum(r["content_sentences"] for r in rows))
        judged = [r["judge"] for r in rows if r.get("judge") and "error" not in r["judge"]]
        if judged:
            m["judge_grounded"] = _ratio(sum(1 for j in judged if j.get("grounded")), len(judged))
            m["judge_score"] = round(sum(j["score"] for j in judged) / len(judged), 4)
            m["judge_n"] = len(judged)
    return m


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def sweep(spec: str, rows: list[dict]) -> str:
    """No-answer accuracy vs false abstains across thresholds on the top hit's signal (dev split only)."""
    key = {"bm25": "bm25", "vector": "cos"}.get(spec.split(":")[0], "rerank")
    vals = sorted({r["top_signals"].get(key, 0.0) for r in rows})
    lines = [f"threshold on top {key}: no-answer / false-abstain"]
    for t in vals:
        na = sum(1 for r in rows if not r["gold"] and r["top_signals"].get(key, 0) < t)
        fa = sum(1 for r in rows if r["gold"] and r["top_signals"].get(key, 0) < t)
        lines.append(f"  {t:.4f}: {na}/{sum(1 for r in rows if not r['gold'])} no-answer, {fa} false abstains")
    return "\n".join(lines)


def _pct(v) -> str:
    return "–" if v is None else f"{100 * v:.1f}%"


def render(results: list[dict], cases_path: Path, corpus: rag_corpus.Corpus, answers: str | None) -> str:
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    n = results[0]["metrics"]
    lines = [
        f"# Retrieval eval: {cases_path.name}", "",
        f"Run {when}. {n['n_answerable']} answerable questions + {n['n_no_answer']} with no answer in the corpus; "
        f"corpus {len(corpus.docs)} documents, {len(corpus.chunks)} chunks ({len(corpus.dropped)} duplicates dropped), "
        f"fingerprint `{corpus.fingerprint}`. Recall@k and MRR are on the ranking before the no-answer cut; "
        f"definitions in evals/rag/run.py.", "",
        "| Config | R@1 | R@3 | R@5 | R@10 | MRR | No-answer | False abstain | p50 ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        m = r["metrics"]
        lines.append(f"| {r['label']} | {_pct(m['recall@1'])} | {_pct(m['recall@3'])} | {_pct(m['recall@5'])} | "
                     f"{_pct(m['recall@10'])} | {m['mrr']:.3f} | {_pct(m['no_answer'])} | {_pct(m['false_abstain'])} "
                     f"| {r['p50_ms']} |")
    if answers:
        lines += ["", f"Answers ({'LLM' if answers == 'live' else 'offline templates'}, top 3 chunks):", "",
                  "| Config | Citation accuracy | Bad citations | Faithfulness (lexical) | Judge grounded | Judge score "
                  "| Judged | LLM errors |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in results:
            m = r["metrics"]
            js = f"{m['judge_score']:.3f}" if "judge_score" in m else "–"
            lines.append(f"| {r['label']} | {_pct(m['citation_accuracy'])} | {_pct(m['bad_citations'])} | "
                         f"{_pct(m['faithfulness'])} | {_pct(m.get('judge_grounded'))} | {js} | {m.get('judge_n', '–')} "
                         f"| {m.get('llm_errors', 0)} |")
    last = results[-1]
    misses = [c for c in last["cases"] if c["gold"] and (not c["first_gold_rank"] or c["first_gold_rank"] > 5)]
    lines += ["", f"Misses at k=5 for {last['label']} ({len(misses)}):", ""]
    lines += [f"- {c['id']} {c['q']} (gold {', '.join(c['gold'][:2])}; got {', '.join(c['top'][:3])})" for c in misses]
    wrong_none = [c for c in last["cases"] if not c["gold"] and c["returned"]]
    if wrong_none:
        lines += ["", "Off-corpus questions that still got chunks:", ""]
        lines += [f"- {c['id']} {c['q']} -> {', '.join(c['returned'])}" for c in wrong_none]
    return "\n".join(lines) + "\n"


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Retrieval evals for search_docs")
    ap.add_argument("--cases", type=Path, default=DEV)
    ap.add_argument("--configs", nargs="*", default=OFFLINE_CONFIGS)
    ap.add_argument("--answers", choices=["offline", "live"], default="offline")
    ap.add_argument("--out", type=Path, default=Path("evals/results/rag_dev.json"))
    ap.add_argument("--report", type=Path, default=Path("evals/report_rag_dev.md"))
    ap.add_argument("--gate", action="store_true", help=f"fail unless {SHIPPED_OFFLINE} meets GATES")
    ap.add_argument("--concurrency", type=int, default=1, help="answers written at once (live runs)")
    ap.add_argument("--sweep", metavar="CONFIG", help="print a no-answer threshold sweep (dev split only)")
    args = ap.parse_args(argv)

    s = Settings()
    corpus = rag_corpus.load(s.docs_dir)
    cases = load_cases(args.cases, corpus.chunks)
    if args.sweep:
        if args.cases.resolve() != DEV.resolve():
            raise SystemExit("--sweep tunes thresholds: dev split only")
        res = await run_config(args.sweep, cases, corpus, s, None)
        print(sweep(args.sweep, res["cases"]))
        return 0
    llm = None
    if args.answers == "live":
        from app.llm import LLMClient

        llm = LLMClient(s)
    results = []
    for spec in args.configs:
        results.append(await run_config(spec, cases, corpus, s, args.answers, llm, args.concurrency))
        m = results[-1]["metrics"]
        print(f"{results[-1]['label']:52} R@5 {_pct(m['recall@5']):>6}  MRR {m['mrr']:.3f}  "
              f"no-answer {_pct(m['no_answer'])}  cite {_pct(m.get('citation_accuracy'))}", file=sys.stderr)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"cases_file": str(args.cases), "corpus": corpus.fingerprint,
                                    "run_at": datetime.now(UTC).isoformat(), "results": results}, indent=1))
    args.report.write_text(render(results, args.cases, corpus, args.answers))
    if args.gate:
        shipped = next((r for r in results if r["config"] == SHIPPED_OFFLINE), None)
        if shipped is None:
            raise SystemExit(f"--gate needs {SHIPPED_OFFLINE} in --configs")
        m = shipped["metrics"]
        failed = [f"{k} {m[k]} (gate {v})" for k, v in GATES.items()
                  if m.get(k) is None or (m[k] > v if k == "bad_citations" else m[k] < v)]
        if failed:
            print("RAG gate failed: " + "; ".join(failed), file=sys.stderr)
            return 1
        print(f"gate passed ({SHIPPED_OFFLINE})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
