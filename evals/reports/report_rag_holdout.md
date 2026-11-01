# Retrieval eval: cases_holdout.yaml

Run 2026-10-20 19:30 UTC. 30 answerable questions + 3 with no answer in the corpus; corpus 111 documents, 2191 chunks (6 duplicates dropped), fingerprint `062a8d14e296c069`. Recall@k and MRR are on the ranking before the no-answer cut; definitions in evals/rag/run.py.

| Config | R@1 | R@3 | R@5 | R@10 | MRR | No-answer | False abstain | p50 ms |
|---|---|---|---|---|---|---|---|---|
| BM25 only | 40.0% | 60.0% | 70.0% | 83.3% | 0.533 | 66.7% | 6.7% | 0.92 |
| vector only, hash embedder | 16.7% | 30.0% | 50.0% | 60.0% | 0.293 | 33.3% | 3.3% | 3.85 |
| hybrid (RRF), hash embedder | 26.7% | 50.0% | 66.7% | 83.3% | 0.425 | 66.7% | 3.3% | 3.01 |
| hybrid + rerank, hash embedder, lexical rerank | 50.0% | 70.0% | 80.0% | 86.7% | 0.630 | 66.7% | 0.0% | 8.35 |

Answers (offline templates, top 3 chunks):

| Config | Citation accuracy | Bad citations | Faithfulness (lexical) | Judge grounded | Judge score | Judged | LLM errors |
|---|---|---|---|---|---|---|---|
| BM25 only | 46.7% | 0.0% | 69.4% | – | – | – | 0 |
| vector only, hash embedder | 13.3% | 0.0% | 62.3% | – | – | – | 0 |
| hybrid (RRF), hash embedder | 43.3% | 0.0% | 64.6% | – | – | – | 0 |
| hybrid + rerank, hash embedder, lexical rerank | 70.0% | 0.0% | 65.2% | – | – | – | 0 |

Misses at k=5 for hybrid + rerank, hash embedder, lexical rerank (6):

- h06 What is an extended public key in an HD wallet? (gold bip-0032#6; got wiki-crypto-wallet#15, bip-0032#16, bip-0032#27)
- h07 When is taproot key path spending used? (gold bip-0341#11; got bip-0341#28, bip-0341#37, bip-0341#31)
- h13 What are the risks of using a cross-chain bridge? (gold eth-docs-bridges#9; got eth-docs-bridges#17, eth-docs-bridges#14, eth-docs-bridges#16)
- h18 What triggered Ethereum's switch from proof of work to proof of stake? (gold eip-3675#2; got eth-docs-pow#0, eth-docs-pos#0, wiki-ethereum#10)
- h27 What have Tether's reserves been made of? (gold wiki-tether#32; got wiki-tether#30, wiki-tether#9, wiki-tether#0)
- h29 How does a pump and dump scheme work? (gold wiki-pump-and-dump#0; got wiki-pump-and-dump#12, wiki-pump-and-dump#14, wiki-pump-and-dump#10)

Off-corpus questions that still got chunks:

- h33 Who is the current CEO of Apple? -> erc-4337#34
