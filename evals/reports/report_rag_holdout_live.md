# Retrieval eval: cases_holdout.yaml

Run 2026-10-20 19:54 UTC. 30 answerable questions + 3 with no answer in the corpus; corpus 111 documents, 2191 chunks (6 duplicates dropped), fingerprint `062a8d14e296c069`. Recall@k and MRR are on the ranking before the no-answer cut; definitions in evals/rag/run.py.

| Config | R@1 | R@3 | R@5 | R@10 | MRR | No-answer | False abstain | p50 ms |
|---|---|---|---|---|---|---|---|---|
| hybrid + rerank, hash embedder, lexical rerank | 50.0% | 70.0% | 80.0% | 86.7% | 0.630 | 66.7% | 0.0% | 14.42 |
| hybrid + rerank, bge-small, MiniLM cross-encoder | 66.7% | 73.3% | 80.0% | 90.0% | 0.721 | 100.0% | 0.0% | 4218.54 |

Answers (LLM, top 3 chunks):

| Config | Citation accuracy | Bad citations | Faithfulness (lexical) | Judge grounded | Judge score | Judged | LLM errors |
|---|---|---|---|---|---|---|---|
| hybrid + rerank, hash embedder, lexical rerank | 70.0% | 0.0% | 34.4% | 92.3% | 0.969 | 26 | 0 |
| hybrid + rerank, bge-small, MiniLM cross-encoder | 73.3% | 0.0% | 31.2% | 91.7% | 0.975 | 24 | 0 |

Misses at k=5 for hybrid + rerank, bge-small, MiniLM cross-encoder (6):

- h05 How does Bitcoin keep block times steady as hashing power grows? (gold bitcoin-whitepaper#7; got wiki-bitcoin#20, wiki-bitcoin#15, ethereum-whitepaper#9)
- h11 What fields does an Ethereum transaction include? (gold eth-docs-transactions#2; got eth-docs-transactions#15, eth-docs-transactions#16, eth-docs-transactions#12)
- h17 What does a bundler do in ERC-4337 account abstraction? (gold erc-4337#0; got erc-4337#41, erc-4337#31, erc-4337#15)
- h18 What triggered Ethereum's switch from proof of work to proof of stake? (gold eip-3675#2; got wiki-ethereum#10, eth-docs-pos#0, eth-docs-pow#0)
- h27 What have Tether's reserves been made of? (gold wiki-tether#32; got wiki-tether#26, wiki-tether#20, wiki-tether#30)
- h30 How does short selling work? (gold wiki-short-selling#0; got wiki-short-selling#3, wiki-short-selling#4, wiki-short-selling#33)
