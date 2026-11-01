# Retrieval eval: cases_dev.yaml

Run 2026-10-20 11:50 UTC. 58 answerable questions + 4 with no answer in the corpus; corpus 111 documents, 2191 chunks (6 duplicates dropped), fingerprint `062a8d14e296c069`. Recall@k and MRR are on the ranking before the no-answer cut; definitions in evals/rag/run.py.

| Config | R@1 | R@3 | R@5 | R@10 | MRR | No-answer | False abstain | p50 ms |
|---|---|---|---|---|---|---|---|---|
| BM25 only | 43.1% | 70.7% | 79.3% | 89.7% | 0.572 | 50.0% | 0.0% | 1.31 |
| vector only, hash embedder | 27.6% | 53.4% | 67.2% | 74.1% | 0.420 | 25.0% | 0.0% | 3.73 |
| hybrid (RRF), hash embedder | 34.5% | 69.0% | 74.1% | 86.2% | 0.526 | 75.0% | 0.0% | 3.98 |
| hybrid + rerank, hash embedder, lexical rerank | 51.7% | 74.1% | 81.0% | 86.2% | 0.632 | 75.0% | 0.0% | 9.17 |
| vector only, bge-small | 55.2% | 79.3% | 84.5% | 93.1% | 0.685 | 75.0% | 0.0% | 27.9 |
| hybrid (RRF), bge-small | 51.7% | 77.6% | 86.2% | 96.5% | 0.667 | 75.0% | 0.0% | 30.58 |
| hybrid + rerank, bge-small, lexical rerank | 58.6% | 81.0% | 86.2% | 93.1% | 0.704 | 75.0% | 1.7% | 32.1 |
| hybrid + rerank, hash embedder, MiniLM cross-encoder | 62.1% | 84.5% | 91.4% | 94.8% | 0.743 | 100.0% | 1.7% | 4289.85 |
| hybrid + rerank, bge-small, MiniLM cross-encoder | 62.1% | 84.5% | 87.9% | 96.5% | 0.743 | 100.0% | 0.0% | 3182.01 |

Answers (offline templates, top 3 chunks):

| Config | Citation accuracy | Bad citations | Faithfulness (lexical) | Judge grounded | Judge score |
|---|---|---|---|---|---|
| BM25 only | 62.1% | 0.0% | 68.3% | – | – |
| vector only, hash embedder | 36.2% | 0.0% | 66.6% | – | – |
| hybrid (RRF), hash embedder | 63.8% | 0.0% | 68.6% | – | – |
| hybrid + rerank, hash embedder, lexical rerank | 70.7% | 0.0% | 65.3% | – | – |
| vector only, bge-small | 74.1% | 0.0% | 63.7% | – | – |
| hybrid (RRF), bge-small | 72.4% | 0.0% | 65.3% | – | – |
| hybrid + rerank, bge-small, lexical rerank | 75.9% | 0.0% | 62.0% | – | – |
| hybrid + rerank, hash embedder, MiniLM cross-encoder | 75.9% | 0.0% | 69.2% | – | – |
| hybrid + rerank, bge-small, MiniLM cross-encoder | 77.6% | 0.0% | 68.6% | – | – |

Misses at k=5 for hybrid + rerank, bge-small, MiniLM cross-encoder (7):

- d15 Why would a wallet use hardened child keys instead of normal ones? (gold bip-0032#10; got bip-0032#6, bip-0032#27, bip-0032#9)
- d17 How does a bitcoin transaction say it can be replaced by one paying a higher fee? (gold bip-0125#0; got bip-0125#7, bip-0125#6, bip-0125#2)
- d18 What is the block weight limit that came with segwit? (gold bip-0141#11, bip-0141#12; got bip-0341#42, wiki-bitcoin#5, bip-0341#41)
- d28 Why can't smart contracts just read off-chain data themselves? (gold eth-docs-oracles#8, eth-docs-oracles#9; got eth-docs-smart-contracts#6, eth-docs-oracles#6, eth-docs-oracles#19)
- d31 Which functions does an ERC-20 token contract have to implement? (gold eth-docs-erc20#2, erc-20#5; got wiki-ethereum#23, eth-docs-erc20#6, eth-docs-erc20#7)
- d34 How does Ethereum stop a contract from running forever? (gold ethereum-whitepaper#37, ethereum-whitepaper#35; got ethereum-whitepaper#60, ethereum-whitepaper#45, wiki-smart-contract#6)
- d47 How long does a Binance websocket connection stay open? (gold binance-web-socket-streams#1; got binance-web-socket-streams#2, binance-web-socket-streams#4, binance-web-socket-streams#19)
