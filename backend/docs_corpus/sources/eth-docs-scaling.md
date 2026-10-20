---
title: Scaling (Ethereum)
url: https://ethereum.org/en/developers/docs/scaling/
license: MIT (ethereum/ethereum-org-website LICENSE)
format: markdown
fetched_at: 2026-10-20T07:37:46Z
sha256: 5f6d08d0bd9df6387f94b570282c7237b5573902f85df05dc5d0e26e96bd3ec5
---
## Scaling overview

As the number of people using Ethereum has grown, the blockchain has reached certain capacity limitations. This has driven up the cost of using the network, creating the need for "scaling solutions." There are multiple solutions being researched, tested and implemented that take different approaches to achieve similar goals.

The main goal of scalability is to increase transaction speed (faster finality) and transaction throughput (higher number of transactions per second) without sacrificing decentralization or security. On the layer 1 Ethereum blockchain, high demand leads to slower transactions and nonviable gas prices. Increasing the network capacity in terms of speed and throughput is fundamental to the meaningful and mass adoption of Ethereum.

While speed and throughput are important, it is essential that scaling solutions enabling these goals remain decentralized and secure. Keeping the barrier to entry low for node operators is critical in preventing a progression towards centralized and insecure computing power.

Conceptually we first categorize scaling as either onchain scaling or offchain scaling.

## Prerequisites

You should have a good understanding of all the foundational topics. Implementing scaling solutions is advanced as the technology is less battle-tested, and continues to be researched and developed.

## Onchain scaling

Onchain scaling requires changes to the Ethereum protocol (layer 1 Mainnet). For a long time, sharding the blockchain was expected to scale Ethereum. This was going to involve splitting the blockchain into discrete pieces (shards) to be verified by subsets of validators. However, scaling by layer-2 rollups has taken over as the primary scaling technique. This is supported by the addition of a new cheaper form of data attached to Ethereum blocks that is specially designed to make rollups cheap for users.

### Sharding

Sharding is the process of splitting a database. Subsets of validators would be responsible for individual shards rather than keeping track of all of Ethereum. Sharding was on the Ethereum roadmap for a long time, and was once intended to be shipped before The Merge to proof-of-stake. However, the rapid development of layer 2 rollups and the invention of Danksharding (adding blobs of rollup data to Ethereum blocks that can be very efficiently verified by validators) has led the Ethereum community to favour rollup-centric scaling instead of scaling by sharding. This will also help to keep Ethereum's consensus logic simpler.

## Offchain scaling

Offchain solutions are implemented separately from layer 1 Mainnet - they require no changes to the existing Ethereum protocol. Some solutions, known as "layer 2" solutions, derive their security directly from layer 1 Ethereum consensus, such as optimistic rollups, zero-knowledge rollups or state channels. Other solutions involve the creation of new chains in various forms that derive their security separately from Mainnet, such as sidechains, validiums, or plasma chains. These solutions communicate with Mainnet but derive their security differently to obtain a variety of goals.

### Layer 2 scaling

This category of offchain solutions derives its security from Mainnet Ethereum.

Layer 2 is a collective term for solutions designed to help scale your application by handling transactions off the Ethereum Mainnet (layer 1) while taking advantage of the robust decentralized security model of Mainnet. Transaction speed suffers when the network is busy, making the user experience poor for certain types of dapps. And as the network gets busier, gas prices increase as transaction senders aim to outbid each other. This can make using Ethereum very expensive.

Most layer 2 solutions are centered around a server or cluster of servers, each of which may be referred to as a node, validator, operator, sequencer, block producer, or similar term. Depending on the implementation, these layer 2 nodes may be run by the individuals, businesses or entities that use them, or by a 3rd party operator, or by a large group of individuals (similar to Mainnet). Generally speaking, transactions are submitted to these layer 2 nodes instead of being submitted directly to layer 1 (Mainnet). For some solutions, the layer 2 instance then batches them into groups before anchoring them to layer 1, after which they are secured by layer 1 and cannot be altered. The details of how this is done vary significantly between different layer 2 technologies and implementations.

A specific layer 2 instance may be open and shared by many applications, or may be deployed by one project and dedicated to supporting only their application.

#### Why is layer 2 needed?

- Increased transactions per second greatly improves user experience, and reduces network congestion on Mainnet Ethereum.
- Transactions are rolled up into a single transaction to Mainnet Ethereum, reducing gas fees for users and making Ethereum more inclusive and accessible for people everywhere.
- Any updates to scalability should not be at the expense of decentralization or security – layer 2 builds on top of Ethereum.
- There are application-specific layer 2 networks that bring their own set of efficiencies when working with assets at scale.

More on layer 2.

#### Rollups

Rollups perform transaction execution outside layer 1 and then the data is posted to layer 1 where consensus is reached. As transaction data is included in layer 1 blocks, this allows rollups to be secured by native Ethereum security.

There are two types of rollups with different security models:

- **Optimistic rollups**: assumes transactions are valid by default and only runs computation, via a **fraud proof**, in the event of a challenge. More on Optimistic rollups.
- **Zero-knowledge rollups**: runs computation offchain and submits a **validity proof** to the chain. More on zero-knowledge rollups.

#### State channels

State channels utilize multisig contracts to enable participants to transact quickly and freely offchain, then settle finality with Mainnet. This minimizes network congestion, fees, and delays. The two types of channels are currently state channels and payment channels.

Learn more about state channels.

### Sidechains

A sidechain is an independent EVM-compatible blockchain that runs in parallel to Mainnet. These are compatible with Ethereum via two-way bridges and run under their own chosen rules of consensus and block parameters.

Learn more about Sidechains.

### Plasma

A plasma chain is a separate blockchain that is anchored to the main Ethereum chain and uses fraud proofs (like optimistic rollups) to arbitrate disputes.

Learn more about Plasma.

### Validium

A Validium chain uses validity proofs like zero-knowledge rollups but data is not stored on the main layer 1 Ethereum chain. This can lead to 10k transactions per second per Validium chain and multiple chains can be run in parallel.

Learn more about Validium.

## Why are so many scaling solutions needed?

- Multiple solutions can help reduce the overall congestion on any one part of the network and also prevent single points of failure.
- The whole is greater than the sum of its parts. Different solutions can exist and work in harmony, allowing for an exponential effect on future transaction speed and throughput.
- Not all solutions require utilizing the Ethereum consensus algorithm directly, and alternatives can offer benefits that would otherwise be difficult to obtain.

## More of a visual learner?

_Note the explanation in the video uses the term "Layer 2" to refer to all offchain scaling solutions, while we differentiate "Layer 2" as an offchain solution that derives its security through layer 1 Mainnet consensus._

## Further reading

- A rollup-centric Ethereum roadmap _Vitalik Buterin_
- Up-to-date analytics on Layer 2 scaling solutions for Ethereum
- Evaluating Ethereum layer 2 Scaling Solutions: A Comparison Framework
- An Incomplete Guide to Rollups
- Ethereum-powered ZK-Rollups: World Beaters
- Optimistic Rollups vs ZK Rollups
- Why rollups + data shards are the only sustainable solution for high scalability
- What kind of Layer 3s make sense?
- Data Availability Or: How Rollups Learned To Stop Worrying And Love Ethereum
- The Practical Guide to Ethereum Rollups

_Know of a community resource that helped you? Edit this page and add it!_

## Tutorials: Build scalable Layer 2s on Ethereum

- All you can cache _– How to build and use a caching contract to reduce calldata costs on rollups._
- Short ABIs for Calldata Optimization _– How to use shorter ABIs to reduce calldata costs for layer 2 transactions._
