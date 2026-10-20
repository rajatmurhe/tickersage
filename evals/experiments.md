# TickerSage experiments

Updated 2026-10-20 UTC.

<!-- exp:routing -->
## 1. Routed vs single strong model

Held-out v4 (33 cases) on Groq's free tier. The routed row is v4's first live run; the single row ran right after it on the same cases. Groq's free tier caps each model at 200K tokens a day, which is why this uses v4 rather than the 110-case set. One difference: the single run already had the fix for space-grouped thousands (failure 9 in the README), which only changes how many drafts the verifier sends back.

33 cases, provider `groq`, provider fallback off, judge: nvidia/nemotron-3-super-120b-a12b. Run 2026-10-20T05:45:57+00:00.

| Config | Pass rate | Faithfulness | Judge | p50 ms | p95 ms | Cost / query | Models |
|---|---|---|---|---|---|---|---|
| routed (gpt-oss-20b routes, 120b writes) | 78.8% | 100.0% | 87.9% | 729.9 | 1679.7 | $0.00000 | openai/gpt-oss-120b, openai/gpt-oss-20b |
| single (gpt-oss-120b everywhere) | 78.8% | 100.0% | 88.5% | 1090.2 | 4503.1 | $0.00000 | openai/gpt-oss-120b |

<!-- exp:verifier -->
## 2. Number verifier on vs off

110 cases, provider `nvidia`, provider fallback off, judge: nvidia/nemotron-3-super-120b-a12b. Run 2026-10-20T05:43:17+00:00.

| Config | Pass rate | Faithfulness | Judge | p50 ms | p95 ms | Cost / query | Models |
|---|---|---|---|---|---|---|---|
| verifier on | 80.0% | 100.0% | 95.3% | 2222.3 | 7700.9 | $0.00000 | nvidia/nemotron-3-super-120b-a12b |
| verifier off | 78.2% | 99.7% | 95.5% | 2339.2 | 11011.9 | $0.00000 | nvidia/nemotron-3-super-120b-a12b |

With the verifier off, 1 of 318 numbers that reached users was unsupported: explain-12 said Ethereum "underperformed by about 3.7 percentage points", a subtraction the model did itself (6.58 − 2.9; no tool returned it). With the verifier on, that draft was rejected and the user got the deterministic figures.

<!-- exp:cache -->
## 3. Market-data cache on vs off

24 price, comparison and explain-move questions against live public APIs, asked twice in a row. Timings are for the second pass (LLM offline, so this isolates tool latency).

| Cache | p50 ms | p95 ms | mean ms |
|---|---|---|---|
| on | 5.0 | 6.4 | 5.2 |
| off | 120.5 | 315.8 | 160.9 |

Median speed-up with the cache warm: 24x.

<!-- exp:providers -->
## providers

Skipped: needs ANTHROPIC_API_KEY and OPENAI_API_KEY.

<!-- exp:rag -->
## 5. Docs retrieval ablation: BM25 vs vector vs hybrid vs hybrid + rerank

`python -m evals.rag.run` on the held-out retrieval set (30 answerable + 3 off-corpus questions; first run, after
all tuning on the dev split). Corpus: 111 documents, 2,191 chunks. Answer columns are offline template answers
from the top 3 chunks. Dev-split numbers and per-case misses: `evals/report_rag_dev_ablation.md`,
`evals/report_rag_holdout_ablation.md`; write-up: `evals/history/10-docs-rag-holdout-first-run.md`.

| Config | R@1 | R@5 | R@10 | MRR | Off-corpus abstained | Citation accuracy | Bad citations |
|---|---|---|---|---|---|---|---|
| BM25 only | 40.0% | 70.0% | 83.3% | 0.533 | 2/3 | 46.7% | 0.0% |
| vector only, hash embedder | 16.7% | 50.0% | 60.0% | 0.293 | 1/3 | 13.3% | 0.0% |
| vector only, bge-small | 53.3% | 83.3% | 86.7% | 0.668 | 3/3 | 76.7% | 0.0% |
| hybrid (RRF), hash embedder | 26.7% | 66.7% | 83.3% | 0.425 | 2/3 | 43.3% | 0.0% |
| hybrid (RRF), bge-small | 53.3% | 80.0% | 90.0% | 0.651 | 2/3 | 66.7% | 0.0% |
| hybrid + lexical rerank, hash embedder | 50.0% | 80.0% | 86.7% | 0.630 | 2/3 | 70.0% | 0.0% |
| hybrid + lexical rerank, bge-small | 60.0% | 80.0% | 83.3% | 0.700 | 3/3 | 76.7% | 0.0% |
| hybrid + MiniLM cross-encoder, hash embedder | 66.7% | 83.3% | 90.0% | 0.726 | 3/3 | 73.3% | 0.0% |
| hybrid + MiniLM cross-encoder, bge-small | 66.7% | 80.0% | 90.0% | 0.721 | 3/3 | 73.3% | 0.0% |

Fusion alone didn't beat its best leg; the reranker is where the gain came from (R@1 53.3% → 66.7% with the
cross-encoder). With 30 questions, one question is 3.3 points, so the gaps among the four reranked configs are within noise.

Live answers on the same set (Nemotron 3 Ultra writes and judges): citation accuracy 70.0% (offline config) and
73.3% (bge-small + cross-encoder), 0.0% bad citations in both, judge "grounded" 92.3% and 91.7%. The ungrounded
answers were all retrieval misses (`evals/report_rag_holdout_live.md`).

