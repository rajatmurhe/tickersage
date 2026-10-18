"""Symbol whitelist, normalisation and extraction from free text.

The whitelist comes from Binance `/api/v3/exchangeInfo` (TRADING spot pairs), cached
for a day. If that call fails we fall back to a static list of large assets so the
agent still works for the common cases.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .tools.errors import ToolError

DEFAULT_QUOTE = "USDT"
STABLECOINS = {"USDT", "USDC", "FDUSD", "DAI", "TUSD", "USDP", "BUSD", "USDE"}
QUOTES = ("USDT", "FDUSD", "USDC", "BTC", "ETH", "BNB", "EUR", "TRY")

# Base asset -> CoinGecko id, used for the backup data source.
COINGECKO_IDS: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin", "XRP": "ripple",
    "ADA": "cardano", "DOGE": "dogecoin", "AVAX": "avalanche-2", "DOT": "polkadot", "LINK": "chainlink",
    "LTC": "litecoin", "TRX": "tron", "TON": "the-open-network", "SHIB": "shiba-inu", "PEPE": "pepe",
    "XLM": "stellar", "BCH": "bitcoin-cash", "NEAR": "near", "UNI": "uniswap", "ATOM": "cosmos",
    "ETC": "ethereum-classic", "FIL": "filecoin", "APT": "aptos", "SUI": "sui", "ARB": "arbitrum",
    "OP": "optimism", "INJ": "injective-protocol", "HBAR": "hedera-hashgraph", "POL": "polygon-ecosystem-token",
}

ASSET_NAMES: dict[str, str] = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "BNB": "BNB", "XRP": "XRP", "ADA": "Cardano",
    "DOGE": "Dogecoin", "AVAX": "Avalanche", "DOT": "Polkadot", "LINK": "Chainlink", "LTC": "Litecoin",
    "TRX": "TRON", "TON": "Toncoin", "SHIB": "Shiba Inu", "PEPE": "Pepe", "XLM": "Stellar",
    "BCH": "Bitcoin Cash", "NEAR": "NEAR Protocol", "UNI": "Uniswap", "ATOM": "Cosmos",
    "ETC": "Ethereum Classic", "FIL": "Filecoin", "APT": "Aptos", "SUI": "Sui", "ARB": "Arbitrum",
    "OP": "Optimism", "INJ": "Injective", "HBAR": "Hedera", "POL": "Polygon",
}

# Names and tickers that are safe to match case-insensitively (not common English words).
NAME_ALIASES: dict[str, str] = {
    "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "ether": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL", "binance coin": "BNB", "bnb": "BNB", "ripple": "XRP", "xrp": "XRP",
    "cardano": "ADA", "ada": "ADA", "dogecoin": "DOGE", "doge": "DOGE", "avalanche": "AVAX", "avax": "AVAX",
    "polkadot": "DOT", "chainlink": "LINK", "litecoin": "LTC", "ltc": "LTC", "tron": "TRX", "trx": "TRX",
    "toncoin": "TON", "shiba inu": "SHIB", "shib": "SHIB", "pepe": "PEPE", "stellar": "XLM", "xlm": "XLM",
    "bitcoin cash": "BCH", "bch": "BCH", "uniswap": "UNI", "cosmos": "ATOM", "ethereum classic": "ETC",
    "filecoin": "FIL", "aptos": "APT", "arbitrum": "ARB", "optimism": "OP", "injective": "INJ",
    "hedera": "HBAR", "hbar": "HBAR", "polygon": "POL", "matic": "POL",
}

STATIC_PAIRS = frozenset(f"{base}{DEFAULT_QUOTE}" for base in COINGECKO_IDS)

# Upper-case words that are listed as tokens somewhere but are almost always English or finance jargon.
_UPPER_STOP = set(
    """A I AI AM AN AND ANY ARE AS AT ATH ATL BE BUY BY CAN CEO CEX CPI DATA DCA DEX DO DM ETF EU FAQ FED
    FOMC FOMO FOR FUD GDP GET GO HIGH HODL HOW IF IMO IN IS IT JSON KYC LLM LOW MA MACD ME MY NEWS NFT NO NOT
    NOW OF OK ON ONE OPEN OR ORDER PM PNL PUT Q1 Q2 Q3 Q4 ROI RSI RULES SEC SELL SMA EMA SO SYSTEM THE TO
    TODAY TVL UK UP US USA USD UTC VS VWAP WE WHAT WHY WILL YOU YOUR APY APR ATR OHLC OHLCV API URL GPT IPO
    ICO P2P YTD BPS PRICE DAN TLDR ASAP FYI PLS PLZ THX LOL ELI5 BTW TBH IDK OMG WTF NGMI WAGMI GM GN
    UPDATE INFO HELP NOTE PS EDIT""".split()
)

_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,11}\b")


@dataclass
class SymbolRegistry:
    pairs: set[str] = field(default_factory=lambda: set(STATIC_PAIRS))
    base_of_pair: dict[str, str] = field(default_factory=dict)
    loader: Callable[[], Awaitable[list[dict]]] | None = None
    loaded: bool = False

    def __post_init__(self) -> None:
        for pair in self.pairs:
            self.base_of_pair.setdefault(pair, _split_pair(pair)[0])

    @property
    def bases(self) -> set[str]:
        return set(self.base_of_pair.values())

    async def ensure_loaded(self) -> None:
        if self.loaded or self.loader is None:
            return
        try:
            symbols = await self.loader()
        except Exception:
            # Keep the static whitelist; the market tools will still raise clean errors.
            self.loaded = True
            return
        if symbols:
            self.load(symbols)
        self.loaded = True

    def load(self, symbols: list[dict]) -> None:
        self.pairs = {s["symbol"] for s in symbols}
        self.base_of_pair = {s["symbol"]: s["baseAsset"] for s in symbols}

    def resolve(self, raw: str) -> str:
        """Map 'btc', 'Bitcoin', 'BTC/USDT' or 'BTCUSDT' to a whitelisted pair."""
        text = raw.strip()
        base = NAME_ALIASES.get(text.lower())
        cleaned = re.sub(r"[\s/_-]", "", text.upper())
        if base:
            cleaned = base
        if cleaned in self.pairs:
            return cleaned
        if f"{cleaned}{DEFAULT_QUOTE}" in self.pairs:
            return f"{cleaned}{DEFAULT_QUOTE}"
        suggestions = difflib.get_close_matches(cleaned, sorted(self.bases), n=3, cutoff=0.75)
        hint = f" Did you mean {', '.join(suggestions)}?" if suggestions else ""
        raise ToolError("unknown_symbol", f"I couldn't find '{raw}' as a trading pair on the exchange.{hint}")

    def base(self, pair: str) -> str:
        return self.base_of_pair.get(pair) or _split_pair(pair)[0]

    def extract(self, text: str) -> list[str]:
        """Base assets mentioned in `text`, in order of first appearance.

        Unknown upper-case tickers ("FAKECOIN") are returned too, after filtering common
        acronyms, so the tool layer can answer with a friendly unknown-symbol error.
        """
        found: list[tuple[int, str]] = []
        lowered = text.lower()
        for alias in sorted(NAME_ALIASES, key=len, reverse=True):
            for m in re.finditer(rf"(?<![\w$]){re.escape(alias)}(?![\w])", lowered):
                found.append((m.start(), NAME_ALIASES[alias]))
        unknown: list[tuple[int, str]] = []
        bases = self.bases
        for m in _TOKEN_RE.finditer(text):
            token = m.group(0)
            if token in _UPPER_STOP:
                continue
            if token in QUOTES and text[max(0, m.start() - 2): m.start()].strip().endswith("/"):
                continue  # the USDT in "BTC/USDT"
            if token in bases:
                found.append((m.start(), token))
            elif token in self.pairs:
                found.append((m.start(), self.base(token)))
            elif len(token) >= 3:
                pair_base = _strip_quote(token)
                if pair_base and pair_base in bases:
                    found.append((m.start(), pair_base))
                else:
                    unknown.append((m.start(), token))
        for m in re.finditer(r"\b([A-Za-z]{2,10})\s*/\s*(USDT|USDC|BTC|ETH)\b", text, re.I):
            base = m.group(1).upper()
            if base in bases:
                found.append((m.start(), base))
        # Unknown tickers are kept so the tools can say "I couldn't find X" instead of ignoring it.
        picked = found + unknown
        out: list[str] = []
        for _, sym in sorted(picked):
            if sym not in out:
                out.append(sym)
        if any(sym not in STABLECOINS for sym in out):
            out = [sym for sym in out if sym not in STABLECOINS]  # "100 USDT worth of DOGE" is about DOGE
        return out


def asset_name(base: str) -> str:
    return ASSET_NAMES.get(base, base)


def _strip_quote(token: str) -> str | None:
    for quote in QUOTES:
        if token.endswith(quote) and len(token) > len(quote):
            return token[: -len(quote)]
    return None


def _split_pair(pair: str) -> tuple[str, str]:
    for quote in QUOTES:
        if pair.endswith(quote) and len(pair) > len(quote):
            return pair[: -len(quote)], quote
    return pair, ""
