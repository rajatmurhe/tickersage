---
title: ERC-20 token standard (ethereum.org)
url: https://ethereum.org/en/developers/docs/standards/tokens/erc-20/
license: MIT (ethereum/ethereum-org-website LICENSE)
format: markdown
fetched_at: 2026-10-20T07:37:48Z
sha256: 76e5936c8997fc52cb1e90b15f6531d03def472ca787e950a232113b035fcd3d
---
## Introduction

**What is a Token?**

Tokens can represent virtually anything in Ethereum:

- reputation points in an online platform
- skills of a character in a game
- financial assets like a share in a company
- a fiat currency like USD
- an ounce of gold
- and more...

Such a powerful feature of Ethereum must be handled by a robust standard, right? That's exactly
where the ERC-20 plays its role! This standard allows developers to build token applications that are interoperable with other products and services. The ERC-20 standard is also used to provide additional functionality to ether.

**What is ERC-20?**

The ERC-20 introduces a standard for Fungible Tokens, in other words, they have a property that makes each Token be exactly
the same (in type and value) as another Token. For example, an ERC-20 Token acts just like the ETH, meaning that 1 Token
is and will always be equal to all the other Tokens.

## Prerequisites

- Accounts
- Smart Contracts
- Token standards

## Body

The ERC-20 (Ethereum Request for Comments 20), proposed by Fabian Vogelsteller in November 2015, is a Token Standard that
implements an API for tokens within Smart Contracts.

Example functionalities ERC-20 provides:

- transfer tokens from one account to another
- get the current token balance of an account
- get the total supply of the token available on the network
- approve whether an amount of token from an account can be spent by a third-party account

If a Smart Contract implements the following methods and events it can be called an ERC-20 Token Contract and, once deployed, it
will be responsible to keep track of the created tokens on Ethereum.

From EIP-20:

### Methods

```solidity
function name() public view returns (string)
function symbol() public view returns (string)
function decimals() public view returns (uint8)
function totalSupply() public view returns (uint256)
function balanceOf(address _owner) public view returns (uint256 balance)
function transfer(address _to, uint256 _value) public returns (bool success)
function transferFrom(address _from, address _to, uint256 _value) public returns (bool success)
function approve(address _spender, uint256 _value) public returns (bool success)
function allowance(address _owner, address _spender) public view returns (uint256 remaining)
```

### Events

```solidity
event Transfer(address indexed _from, address indexed _to, uint256 _value)
event Approval(address indexed _owner, address indexed _spender, uint256 _value)
```

### Examples

Let's see how a Standard is so important to make things simple for us to inspect any ERC-20 Token Contract on Ethereum.
We just need the Contract Application Binary Interface (ABI) to create an interface to any ERC-20 Token. As you can
see below we will use a simplified ABI, to make it a low friction example.

#### Web3.py Example

First, make sure you have installed Web3.py Python library:

```
pip install web3
```

```python
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://cloudflare-eth.com"))

dai_token_addr = "0x6B175474E89094C44Da98b954EedeAC495271d0F"     # DAI
weth_token_addr = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"    # Wrapped ether (WETH)

acc_address = "0xA478c2975Ab1Ea89e8196811F51A7B7Ade33eB11"        # Uniswap V2: DAI 2

# This is a simplified Contract Application Binary Interface (ABI) of an ERC-20 Token Contract.
# It will expose only the methods: balanceOf(address), decimals(), symbol() and totalSupply()
simplified_abi = [
    {
        'inputs': [{'internalType': 'address', 'name': 'account', 'type': 'address'}],
        'name': 'balanceOf',
        'outputs': [{'internalType': 'uint256', 'name': '', 'type': 'uint256'}],
        'stateMutability': 'view', 'type': 'function', 'constant': True
    },
    {
        'inputs': [],
        'name': 'decimals',
        'outputs': [{'internalType': 'uint8', 'name': '', 'type': 'uint8'}],
        'stateMutability': 'view', 'type': 'function', 'constant': True
    },
    {
        'inputs': [],
        'name': 'symbol',
        'outputs': [{'internalType': 'string', 'name': '', 'type': 'string'}],
        'stateMutability': 'view', 'type': 'function', 'constant': True
    },
    {
        'inputs': [],
        'name': 'totalSupply',
        'outputs': [{'internalType': 'uint256', 'name': '', 'type': 'uint256'}],
        'stateMutability': 'view', 'type': 'function', 'constant': True
    }
]

dai_contract = w3.eth.contract(address=w3.to_checksum_address(dai_token_addr), abi=simplified_abi)
symbol = dai_contract.functions.symbol().call()
decimals = dai_contract.functions.decimals().call()
totalSupply = dai_contract.functions.totalSupply().call() / 10**decimals
addr_balance = dai_contract.functions.balanceOf(acc_address).call() / 10**decimals

#  DAI
print("===== %s =====" % symbol)
print("Total Supply:", totalSupply)
print("Addr Balance:", addr_balance)

weth_contract = w3.eth.contract(address=w3.to_checksum_address(weth_token_addr), abi=simplified_abi)
symbol = weth_contract.functions.symbol().call()
decimals = weth_contract.functions.decimals().call()
totalSupply = weth_contract.functions.totalSupply().call() / 10**decimals
addr_balance = weth_contract.functions.balanceOf(acc_address).call() / 10**decimals

#  WETH
print("===== %s =====" % symbol)
print("Total Supply:", totalSupply)
print("Addr Balance:", addr_balance)
```

## Known issues

### ERC-20 token reception issue

**As of 06/20/2024 at least $83,656,418 worth of ERC-20 tokens were lost due to this issue. Note that a pure ERC-20 implementation is prone to this problem unless you implement a set of additional restrictions on top of the standard as listed below.**

When ERC-20 tokens are sent to a smart contract that is not designed to handle ERC-20 tokens, those tokens can be permanently lost. This happens because the receiving contract does not have the functionality to recognize or respond to the incoming tokens, and there’s no mechanism in the ERC-20 standard to notify the receiving contract about the incoming tokens. The main ways this issue takes form is through:

1.	Token transfer mechanism
  - ERC-20 tokens are transferred using the transfer or transferFrom functions
	-	When a user sends tokens to a contract address using these functions, the tokens are transferred regardless of whether the receiving contract is designed to handle them
2.	Lack of notification
	-	The receiving contract does not receive a notification or callback that tokens have been sent to it
	-	If the receiving contract lacks a mechanism to handle tokens (e.g., a fallback function or a dedicated function to manage token reception), the tokens are effectively stuck in the contract’s address
3.	No built-in handling
	-	The ERC-20 standard does not include a mandatory function for receiving contracts to implement, leading to a situation where many contracts are unable to manage incoming tokens properly

**Possible Solutions**

While it is not possible to prevent this issue with ERC-20 completely there are methods that would allow to significantly reduce the possibility of a tokens loss for the end user:

- The most common problem is when a user sends tokens to the token contract address itself (e.g., USDT deposited to the address of USDT token contract). It is recommended to restrict `transfer(..)` function to revert such transfer attempts. Consider adding `require(_to != address(this));` check within the implementation of the `transfer(..)` function.
- The `transfer(..)` function in general is not designed for depositing tokens to contracts. `approve(..) & transferFrom(..)` pattern is used to deposit ERC-20 tokens to contracts instead. It is possible to restrict the transfer function to disallow depositing tokens to any contracts with it, however it may break compatibility with contracts that assume tokens can be deposited to contracts with the `transfer(..)` function (e.g., Uniswap liquidity pools).
- Always assume that ERC-20 tokens can end up in your contract even if your contract is not supposed to ever receive any. There is no way to prevent or reject accidental deposits on the recipients end. It is recommended to implement a function that would allow to extract accidentally deposited ERC-20 tokens.
- Consider using alternative token standards.

Some alternative standards have come out of this issue such as ERC-223 or ERC-1363.

## Further reading

- EIP-20: ERC-20 Token Standard
- OpenZeppelin - Tokens
- OpenZeppelin - ERC-20 Implementation
- Alchemy - Guide to Solidity ERC20 Tokens

## Other fungible token standards

- ERC-223
- ERC-1363
- ERC-777
- ERC-4626 - Tokenized vaults
- ERC-7540 - Asynchronous tokenized vaults

## Tutorials: Build with ERC-20 on Ethereum

- ERC-20 Contract Walk-Through _– A line-by-line annotated walkthrough of the OpenZeppelin ERC-20 contract implementation._
- ERC-20 with Safety Rails _– How to add safeguards to ERC-20 tokens to help users avoid common mistakes._
- Sending Tokens Using ethers.js _– A beginner-friendly guide to transferring ERC-20 tokens using ethers.js._
- Some tricks used by scam tokens and how to detect them _– A deep-dive into scam ERC-20 token patterns and how to identify them._
