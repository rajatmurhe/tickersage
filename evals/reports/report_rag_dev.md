# Retrieval eval: cases_dev.yaml

Run 2026-10-20 19:28 UTC. 58 answerable questions + 4 with no answer in the corpus; corpus 111 documents, 2191 chunks (6 duplicates dropped), fingerprint `062a8d14e296c069`. Recall@k and MRR are on the ranking before the no-answer cut; definitions in evals/rag/run.py.

| Config | R@1 | R@3 | R@5 | R@10 | MRR | No-answer | False abstain | p50 ms |
|---|---|---|---|---|---|---|---|---|
| BM25 only | 43.1% | 70.7% | 79.3% | 89.7% | 0.572 | 50.0% | 0.0% | 1.01 |
| vector only, hash embedder | 27.6% | 53.4% | 67.2% | 74.1% | 0.420 | 25.0% | 0.0% | 2.79 |
| hybrid (RRF), hash embedder | 34.5% | 69.0% | 74.1% | 86.2% | 0.526 | 75.0% | 0.0% | 3.33 |
| hybrid + rerank, hash embedder, lexical rerank | 51.7% | 74.1% | 81.0% | 86.2% | 0.632 | 75.0% | 0.0% | 8.65 |

Answers (offline templates, top 3 chunks):

| Config | Citation accuracy | Bad citations | Faithfulness (lexical) | Judge grounded | Judge score | Judged | LLM errors |
|---|---|---|---|---|---|---|---|
| BM25 only | 62.1% | 0.0% | 68.3% | – | – | – | 0 |
| vector only, hash embedder | 36.2% | 0.0% | 66.6% | – | – | – | 0 |
| hybrid (RRF), hash embedder | 63.8% | 0.0% | 68.6% | – | – | – | 0 |
| hybrid + rerank, hash embedder, lexical rerank | 70.7% | 0.0% | 65.3% | – | – | – | 0 |

Misses at k=5 for hybrid + rerank, hash embedder, lexical rerank (11):

- d10 According to the Bitcoin paper, what motivates nodes to support the network? (gold bitcoin-whitepaper#10; got wiki-bitcoin#2, wiki-bitcoin#0, wiki-lightning#3)
- d13 How does the chance of an attacker rewriting the chain change as more blocks get added? (gold bitcoin-whitepaper#7, bitcoin-whitepaper#16; got wiki-double-spending#4, bitcoin-whitepaper#12, bitcoin-whitepaper#15)
- d17 How does a bitcoin transaction say it can be replaced by one paying a higher fee? (gold bip-0125#0; got bip-0125#7, bip-0125#2, bip-0125#3)
- d28 Why can't smart contracts just read off-chain data themselves? (gold eth-docs-oracles#8, eth-docs-oracles#9; got eth-docs-oracles#19, eth-docs-smart-contracts#6, eth-docs-smart-contracts#8)
- d31 Which functions does an ERC-20 token contract have to implement? (gold eth-docs-erc20#2, erc-20#5; got wiki-ethereum#23, eth-docs-erc20#8, eth-docs-erc20#6)
- d33 What problem is the GHOST protocol in the Ethereum whitepaper meant to fix? (gold ethereum-whitepaper#74, ethereum-whitepaper#75; got ethereum-whitepaper#107, ethereum-whitepaper#77, ethereum-whitepaper#83)
- d34 How does Ethereum stop a contract from running forever? (gold ethereum-whitepaper#37, ethereum-whitepaper#35; got eth-docs-zk-rollups#4, wiki-smart-contract#6, eth-docs-oracles#1)
- d37 How many candles can I fetch in one Binance klines request? (gold binance-rest-api#44; got candlesticks-klines#0, binance-rest-api#46, binance-market-data-api#0)
- d47 How long does a Binance websocket connection stay open? (gold binance-web-socket-streams#1; got binance-web-socket-streams#4, binance-web-socket-streams#2, binance-web-socket-streams#29)
- d52 How are Bollinger Bands calculated? (gold wiki-bollinger-bands#1; got wiki-bollinger-bands#0, wiki-bollinger-bands#5, wiki-bollinger-bands#3)
- d58 What consensus mechanism does Cardano use? (gold wiki-cardano#2; got wiki-proof-of-stake#13, eip-3675#0, eth-docs-pos#0)

Off-corpus questions that still got chunks:

- d61 What is the capital of Australia? -> wiki-stablecoin#43, wiki-binance#22, wiki-stablecoin#26
