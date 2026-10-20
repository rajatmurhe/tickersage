"""Intent classification: a cheap LLM call when available, deterministic rules otherwise.

The rules router doubles as the fallback when the LLM call fails or returns
something unusable, so routing never blocks an answer.
"""

from __future__ import annotations

import json
import logging
import re

from .llm import LLMClient, LLMResponse, LLMUnavailable
from .prompts import ROUTER_SCHEMA, ROUTER_SYSTEM
from .schemas import RouteDecision, Window
from .symbols import NAME_ALIASES, SymbolRegistry

log = logging.getLogger(__name__)
_F = re.I

_WINDOWS: list[tuple[Window, re.Pattern[str]]] = [
    ("30d", re.compile(r"\b(30\s*-?\s*d(ays?)?|month|monthly|four weeks|4 weeks)\b", _F)),
    ("7d", re.compile(r"\b(7\s*-?\s*d(ays?)?|week|weekly|seven days)\b", _F)),
    ("24h", re.compile(r"\b(24\s*-?\s*h(ours?|rs?)?|today|daily|overnight|one day|1\s*d(ay)?)\b", _F)),
]
_COMPARE = re.compile(
    r"\b(compare|comparison|compared|versus|vs\.?|against|relative to|better|worse|outperform\w*|"
    r"underperform\w*|which (one|of|is|has|had|was)|rank\w*|highest|lowest|most|least|biggest|side by side)\b", _F
)
_MOVE_WORDS = (
    r"(drop\w*|dump\w*|fell|fall\w*|down|crash\w*|pump\w*|rall\w*|surg\w*|rise|rising|rose|up|mov\w*|spik\w*|"
    r"tank\w*|plung\w*|jump\w*|slid\w*|slump\w*|sell-?off|bounc\w*|declin\w*|gain\w*|soar\w*|sink\w*|sank|red|green|"
    r"action|swing\w*|volatil\w*|spike\w*)"
)
_EXPLAIN = re.compile(
    rf"\b(why|what('s| is| was)? (driving|behind|causing|caused)|reason\w*|explain\w*|behind|caus\w*|"
    rf"what happened)\b.*\b{_MOVE_WORDS}\b|\bwhy\b.*\b{_MOVE_WORDS}\b|\b{_MOVE_WORDS}\b.*\b(why|news|headlines?|"
    rf"going on|happening)\b|\b(what's|what is|whats)\s+(the\s+)?(news|going on|happening)\b",
    _F,
)
_METRICS = re.compile(
    r"\b(volatil\w*|rsi|relative strength|moving average\w*|sma|ema|vwap|drawdown|momentum|trend\w*|"
    r"return\w*|perform\w*|indicator\w*|overbought|oversold|weekly|monthly|metric\w*|stat\w*|technical\w*|"
    r"analysis|analytics)\b", _F
)
_ORDER_BOOK = re.compile(r"\b(order ?book|spread|depth|liquidity|liquid|bids?|asks?|slippage)\b", _F)
_CONCEPT_START = re.compile(
    r"(^|[,.:;]\s*|\b(can|could) you\s+|\bplease\s+)(what('s| is| are| does| do)|whats|how (does|do|is|are)|explain|"
    r"define|definition|meaning|tell me about|describe|what do .* mean|difference between|what it means)\b|"
    r"^\s*(in simple terms|simply put|eli5)\b", _F
)
_LIVE_WORDS = re.compile(
    r"\b(now|today|current\w*|right now|trading at|price of|this week|this month|last \d+|past \d+|"
    r"24h|24 hours|7 days|30 days|at the moment|latest|so far)\b", _F
)
_CRYPTO_TERMS = re.compile(
    r"\b(crypto\w*|coins?|tokens?|blockchain\w*|exchanges?|binance|defi|stablecoins?|altcoins?|funding rates?|"
    r"perp\w*|futures|halving|staking|mining|wallets?|candles?|candlesticks?|klines?|market caps?|"
    r"market capitali[sz]ation|order ?books?|spreads?|liquidity|volatility|rsi|vwap|moving averages?|drawdowns?|"
    r"open interest|leverage|liquidat\w*|slippage|limit orders?|market orders?|proof of (stake|work)|"
    r"sentiment|bid|ask|trading volume|volume|basis points?|bps|satoshi|gas fees?|layer 2|l2|market depth|depth|"
    r"margin|derivatives?|options?|halvings?|consensus|validators?|miners?|on-?chain|tokenomics|airdrops?|"
    r"dip|hodl|fomo|fud|bull\w*|bear\w*|whales?|rug\s*pull|ath|dca|short squeeze|long squeeze)\b", _F
)
_PRICE_WORDS = re.compile(
    r"\b(price\w*|trading|worth|cost\w*|volume|high|low|change\w*|mov\w*|up|down|market cap|quote)\b", _F
)
_WHAT_IS_ASSET = re.compile(
    r"^\s*(what\s+is|what's|whats|tell\s+me\s+about|explain|describe)\s+(a\s+|an\s+|the\s+)?[\w\s/-]{1,30}\??\s*$"
    r"(?<!\bat\?)(?<!\bat)", _F
)
_NON_CRYPTO = re.compile(
    r"\b(stocks?|shares?|equit\w+|nasdaq|nyse|s&p|dow jones|forex|fx|exchange rates?|gold|oil|gas prices?|houses?|"
    r"rent|groceries|flights?|tickets?|laptops?|phones?|cars?|recipes?|weather)\b", _F
)
_FOREX = re.compile(
    r"\b(fiat\s+)?exchange\s+rates?\b|\b(EUR|GBP|JPY|CHF|CAD|AUD|INR|CNY|USD)\s*(to|/|vs\.?|versus)\s*"
    r"(USD|EUR|GBP|JPY|CHF|CAD|AUD|INR|CNY)\b", _F
)
_EITHER = re.compile(r"\b\w+\s+or\s+\w+\b.*\bwhich\b|\bwhich\b.*\bor\b|^\s*\w+\s+or\s+\w+\b", _F)
_PROPER_NOUN = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-z]+('s)?\b")
_MARKET_WIDE = re.compile(r"\b(crypto|the market|markets|crypto market|altcoins)\b", _F)


def parse_window(text: str) -> Window | None:
    for window, pattern in _WINDOWS:
        if pattern.search(text):
            return window
    return None


def route_rules(question: str, registry: SymbolRegistry) -> RouteDecision:
    q = " ".join(question.split())
    symbols = registry.extract(q)
    window = parse_window(q)
    wants_ob = bool(_ORDER_BOOK.search(q))
    concept_start = bool(_CONCEPT_START.search(q))
    has_term = bool(_CRYPTO_TERMS.search(q))

    def decide(intent, syms=symbols, topic=None) -> RouteDecision:
        return RouteDecision(intent=intent, symbols=syms[:5], window=window,
                             wants_order_book=wants_ob and intent in ("price", "metrics"), topic=topic)

    if _FOREX.search(q) or (_NON_CRYPTO.search(q) and not symbols):
        return decide("out_of_scope", [])
    live = bool(_LIVE_WORDS.search(q) or _EXPLAIN.search(q))
    asks_for_data = bool(symbols) and bool(_PRICE_WORDS.search(q) or _METRICS.search(q) or wants_ob)
    names_only_an_asset = bool(symbols) and bool(_WHAT_IS_ASSET.match(q))
    if concept_start and not live and not asks_for_data and (has_term or names_only_an_asset):
        # "What is a funding rate?", "How does the Bitcoin halving work?", "What is Solana?"
        return decide("concept", [], topic=q)
    if symbols:
        if len(symbols) >= 2 and (_COMPARE.search(q) or _METRICS.search(q) or _EITHER.search(q)):
            return decide("compare")
        if _EXPLAIN.search(q):
            return decide("explain_move", symbols[:1])
        if (_METRICS.search(q) or window in ("7d", "30d")) and not wants_ob:
            return decide("metrics")  # the 24h ticker can't answer a week or month question
        return decide("price")
    if _EXPLAIN.search(q) and _MARKET_WIDE.search(q):
        return decide("explain_move", ["BTC"])
    if has_term:
        return decide("concept", [], topic=q)
    if _PRICE_WORDS.search(q) and not _NON_CRYPTO.search(q) and not _PROPER_NOUN.search(q):
        return decide("price", [])  # "What's the price?" -> the planner asks which asset
    return decide("out_of_scope", [])


def _clean_symbols(raw: list[str], registry: SymbolRegistry, question: str) -> list[str]:
    out: list[str] = []
    for sym in raw:
        s = NAME_ALIASES.get(sym.strip().lower(), sym.strip().upper())
        s = re.sub(r"[^A-Z0-9]", "", s)
        for quote in ("USDT", "USDC", "FDUSD"):
            if s.endswith(quote) and len(s) > len(quote):
                s = s[: -len(quote)]
        if s and s not in out:
            out.append(s)
    known = registry.bases
    extracted = registry.extract(question)
    # Prefer the deterministic extraction when the model returned nothing usable, or named none of the assets the
    # question names: gpt-oss-20b read "Polkadot" as POL (Polygon), and POL's real prices passed the verifier.
    if extracted and (not out or not any(s in known for s in out) or not set(out) & set(extracted)):
        return extracted
    return out


class Router:
    def __init__(self, registry: SymbolRegistry, llm: LLMClient | None) -> None:
        self.registry = registry
        self.llm = llm

    async def route(self, question: str) -> tuple[RouteDecision, LLMResponse | None, str | None]:
        """Returns (decision, llm response if one was made, fallback reason if rules were used)."""
        await self.registry.ensure_loaded()
        if not self.llm or not self.llm.available:
            return route_rules(question, self.registry), None, None
        try:
            resp = await self.llm.generate(
                "fast", ROUTER_SYSTEM, f"<question>{question}</question>",
                json_schema=ROUTER_SCHEMA, max_tokens=2000, effort="low",
            )
            data = json.loads(resp.text)
            decision = RouteDecision(
                intent=data["intent"],
                symbols=_clean_symbols(data.get("symbols") or [], self.registry, question),
                window=data.get("window"),
                wants_order_book=bool(data.get("wants_order_book")),
                topic=data.get("topic"),
                source="llm",
            )
            if decision.intent == "concept":
                decision.symbols = []
            return decision, resp, None
        except LLMUnavailable as exc:
            log.warning("router LLM unavailable, using rules: %s", exc)
            return route_rules(question, self.registry), None, f"llm unavailable: {exc}"
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("router returned unusable output, using rules: %s", exc)
            return route_rules(question, self.registry), None, f"bad router output: {exc}"
