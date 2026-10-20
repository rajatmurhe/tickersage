# Docs RAG: held-out first run and ablation

_2026-10-20. Retrieval eval for `search_docs` after the corpus went from a 24-file glossary to 111 documents
(the glossary plus 87 fetched public sources; `backend/docs_corpus/manifest.yaml`). Full reports:
`evals/report_rag_holdout_ablation.md` (held-out) and `evals/report_rag_dev_ablation.md` (dev)._

## How the sets were made

* `evals/rag/cases_dev.yaml`: 58 answerable questions + 4 with no answer in the corpus.
  `evals/rag/cases_holdout.yaml`: 30 + 3. Both were written on 2026-10-20 from the ingested corpus, **before any
  retrieval code existed**, and committed in `28e27a9` together with the ingest. The held-out file has not been
  edited since.
* Gold is a document id plus a phrase the answering chunk contains. The runner resolves it to chunk ids and fails
  if a phrase matches nothing, so re-chunking can't quietly break the labels.
* Everything tuned was tuned on dev only: BM25 stemming, the lexical reranker's blend and priors, and every
  no-answer threshold. Thresholds follow one rule: the dev threshold that catches the most off-corpus questions
  with zero false abstains. That tuning is committed in `30b3881`. The held-out set was then run once
  (`090bf08`). These are those numbers, unchanged.

## Held-out first run (30 answerable + 3 off-corpus)

| Config | R@1 | R@3 | R@5 | R@10 | MRR | No-answer | False abstain | Citation accuracy |
|---|---|---|---|---|---|---|---|---|
| BM25 only | 40.0% | 60.0% | 70.0% | 83.3% | 0.533 | 2/3 | 6.7% | 46.7% |
| vector only, hash embedder | 16.7% | 30.0% | 50.0% | 60.0% | 0.293 | 1/3 | 3.3% | 13.3% |
| hybrid (RRF), hash embedder | 26.7% | 50.0% | 66.7% | 83.3% | 0.425 | 2/3 | 3.3% | 43.3% |
| **hybrid + lexical rerank, hash embedder** (offline / CI) | 50.0% | 70.0% | 80.0% | 86.7% | 0.630 | 2/3 | 0.0% | 70.0% |
| vector only, bge-small | 53.3% | 80.0% | 83.3% | 86.7% | 0.668 | 3/3 | 0.0% | 76.7% |
| hybrid (RRF), bge-small | 53.3% | 73.3% | 80.0% | 90.0% | 0.651 | 2/3 | 0.0% | 66.7% |
| hybrid + lexical rerank, bge-small | 60.0% | 80.0% | 80.0% | 83.3% | 0.700 | 3/3 | 0.0% | 76.7% |
| hybrid + cross-encoder, hash embedder | 66.7% | 73.3% | 83.3% | 90.0% | 0.726 | 3/3 | 0.0% | 73.3% |
| **hybrid + cross-encoder, bge-small** (Docker config) | 66.7% | 73.3% | 80.0% | 90.0% | 0.721 | 3/3 | 0.0% | 73.3% |

Citation accuracy is on offline template answers built from the top 3 chunks: the share of answerable questions
whose answer cites at least one gold chunk. Bad citations (a `[n]` that maps to no returned chunk) were 0.0% in
every config.

## Dev split, same configs (58 + 4; tuned on, so optimistic)

| Config | R@1 | R@5 | MRR | No-answer | Citation accuracy |
|---|---|---|---|---|---|
| BM25 only | 43.1% | 79.3% | 0.572 | 2/4 | 62.1% |
| hybrid + lexical rerank, hash embedder | 51.7% | 81.0% | 0.632 | 3/4 | 70.7% |
| vector only, bge-small | 55.2% | 84.5% | 0.685 | 3/4 | 74.1% |
| hybrid + cross-encoder, bge-small | 62.1% | 87.9% | 0.743 | 4/4 | 77.6% |

## What it says

* **Reranking is where the gain is.** On held-out, fusion alone was no better than its best leg (hybrid with
  bge-small: MRR 0.651 vs 0.668 for bge-small alone). Adding a cross-encoder lifted R@1 from 53.3% to 66.7% and
  MRR to 0.721–0.726. Fusing in a weak vector leg hurts: hash-embedder hybrid without rerank (0.425) is worse
  than BM25 alone (0.533).
* **The cross-encoder fixes the embedder's weakness.** With a cross-encoder on top, the hash embedder and
  bge-small finish level on held-out (MRR 0.726 vs 0.721; R@5 83.3% vs 80.0%). On 30 questions that difference
  is one question.
* **Abstaining needs a model.** Lexical scores can't tell "capital of Australia" from a real question (the
  Wikipedia articles do mention capital and Australia), so the offline config catches 2 of 3 held-out off-corpus
  questions. Every config with bge-small vectors or the cross-encoder caught 3 of 3 with no false abstains.
* **Recall@10 tops out at 90%.** Held-out misses for the best config are mostly the right document but the
  wrong chunk: h11 (transaction fields), h17 (ERC-4337 bundler), h27 (Tether reserves) and h30 (short selling)
  all ranked other chunks of the gold document first. h05 and h18 found related documents (Wikipedia's Bitcoin
  article, ethereum.org's PoS page) instead of the whitepaper and EIP-3675.
* **Latency.** Cross-encoder configs measured p50 4.9–5.2 s per query here, against 17–22 ms for bge-small
  without it. The machine was an 8 GB M1 at load average 30–90 from other jobs, so treat that as an upper bound,
  not a benchmark. Cold embedding of the 2,191 chunks with bge-small took over 15 minutes under the same load;
  it is cached per chunk after that.

## Side findings

* 23 of 2,191 chunks (1.0%) trip the existing injection redactor on ordinary text ("act as", "behave like",
  "answer with"); those chunks are withheld if retrieved. The redactor is shared with headlines and the red-team
  gate, so it was left as it is.
* The bigger corpus cost the main replay set two concept citations: "What is a stablecoin?" and "What is market
  cap?" now cite the Wikipedia article on the same concept rather than the glossary file the case expects. Pass
  rate 100% → 98.2% (concept citation rate 86.7%). The cases were left unchanged. Held-out v1 and v2 still pass
  100%; all CI gates pass, and the red-team regression set is still 0 of 932 attacks.
