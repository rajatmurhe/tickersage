"""Input and output guardrails.

Input: trade requests, advice/prediction requests and prompt injection are blocked
before any model sees the question. These are deterministic rules, so the refusal
rate on the eval set is reproducible; the LLM router is a second line of defence
(it can also return the `advice` and `trade` intents).

Output: advice wording is stripped from answers and every answer gets a data
timestamp and source line.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

log = logging.getLogger("tickersage.guardrails")

Category = Literal["trade", "advice", "injection", "off_topic"]

_F = re.I

# A question *about* a concept ("what is a limit order?") is not a request to act.
_CONCEPT_QUESTION = re.compile(
    r"^\s*(what|what's|whats|how|why|explain|define|describe|tell me about|is it true|in simple terms)\b", _F
)
_ACT_FOR_ME = re.compile(r"\b(for me|on my behalf|from my (account|wallet)|in my (account|wallet))\b", _F)

_TRADE_VERBS = r"(buy|sell|short|long|place|execute|submit|purchase|swap|convert|transfer|send|withdraw|stake|unstake|trade)"
_NOT_AN_ORDER = (
    r"(?!\s*(-|\s)?(term|answer|summary|version|story|order book|walls?|pressure|side|signals?|volume|offs?|ratio)\b)"
)

TRADE_PATTERNS = [
    # Imperative at the start of a sentence or chained with "and"/"then": "Buy 0.1 BTC", "...and place an order"
    re.compile(rf"(?:^|[.!?;:]\s*|\band\s+|\bthen\s+|\bjust\s+|\bplease\s+){_TRADE_VERBS}\b{_NOT_AN_ORDER}", _F),
    re.compile(rf"\b(can|could|would|will)\s+you\s+(please\s+)?(go\s+)?{_TRADE_VERBS}\b{_NOT_AN_ORDER}", _F),
    re.compile(r"\b(i\s+want|i'd\s+like|i\s+need)\s+you\s+to\s+" + _TRADE_VERBS, _F),
    re.compile(r"\b(market|limit|stop)[- ](buy|sell)\b", _F),
    re.compile(r"\b(execute|submit|place|put in|open|close)\s+(a|an|the|my)?\s*(\w+\s+){0,2}(order|trade|position)s?\b", _F),
    re.compile(r"\b(go|going)\s+(long|short)\s+(on\s+)?\w+", _F),
    re.compile(r"\b\d+\s?x\s+(long|short)\b", _F),
]
ACCOUNT_PATTERNS = [
    re.compile(r"\b(set|place|add|move|put|update|cancel)\s+(a\s+|my\s+|the\s+)?(stop[- ]?loss|take[- ]?profit|trailing\s+stop|"
               r"limit\s+order|order)\b", _F),
    re.compile(r"\bmy\s+(\w+\s+)?(positions?|holdings?|bags?|orders?|funds|coins)\b", _F),
    re.compile(r"\b(api[- ]?key|secret key|private key|seed phrase|mnemonic)\b", _F),
    re.compile(r"\b(connect|link|log ?in)\s+(to\s+)?my\s+(binance\s+|exchange\s+)?(account|wallet)\b", _F),
    re.compile(r"\bmy\s+(binance\s+|exchange\s+)?(account|wallet|balance|portfolio)\b", _F),
]

ADVICE_PATTERNS = [
    re.compile(r"\bshould\s+(i|we)\s+(still\s+)?(buy|sell|hold|invest|short|long|get\s+(in|out)|ape|dca|exit|enter|add|stake|keep|take\s+profits?)\b", _F),
    re.compile(
        r"\b(is|are)\s+(it|now|this|that|today|\w+)\s+(a\s+)?(good|bad|great|smart|safe|right|wise)\s+"
        r"(time\s+to\s+(buy|sell|invest|enter|exit|get\s+in)|idea\s+to\s+(buy|sell|invest)|investment|entry|bet|buy|sell)\b",
        _F,
    ),
    re.compile(r"\b(worth|good)\s+(buying|selling|investing|holding|getting)\b", _F),
    re.compile(r"\bis\s+(it|\w+)\s+a\s+(buy|sell|hold)\b", _F),
    re.compile(r"\b(price\s+)?(prediction|forecast)s?\b", _F),
    re.compile(r"\b(predict|forecast)\w*\b", _F),
    re.compile(r"\b(price\s+target|target\s+price)s?\b", _F),
    re.compile(r"\bwill\s+(\w+\s+){0,3}(go\s+up|go\s+down|rise|fall|drop|crash|pump|dump|moon|recover|rally|bounce|break\s+out|reach|hit|surpass|exceed|go\s+to|be\s+worth|flip|outperform)\b", _F),
    re.compile(r"\b(how\s+much|how\s+high|how\s+low|where)\s+(will|would|could)\s+\w+\s+(\w+\s+){0,2}(be|go|get|trade|reach)\b", _F),
    re.compile(r"\b(guarantee[ds]?|risk[- ]free|sure[- ]?fire|can'?t\s+lose|easy\s+money|\d+x\s+(gains?|returns?|profits?))\b", _F),
    re.compile(r"\b(best|top)\s+(\w+\s+){0,2}(coins?|cryptos?|tokens?|altcoins?|investments?|assets?)\s+(to\s+)?(buy|invest|hold|pick|ape)\b", _F),
    re.compile(r"\bwhat\s+(coins?|cryptos?|tokens?)\s+(should|to)\b", _F),
    re.compile(r"\b(best|top|good|right)\s+(\w+\s+){0,2}to\s+(buy|invest|hold|sell|ape)\b", _F),
    re.compile(r"\bwhich\s+(\w+\s+){0,2}(coins?|cryptos?|tokens?|altcoins?|one)\b.{0,40}\b(buy|invest|hold|pick)\b", _F),
    re.compile(r"\b(tell|show)\s+me\s+what\s+to\s+(buy|sell|invest\s+in)\b", _F),
    re.compile(r"\bwhat\s+(should\s+i|to)\s+(buy|sell|invest\s+in)\b", _F),
    # Broader shapes: any "should I/we ..." about acting on a position, opinion-seeking about direction,
    # "next 100x coin", valuation judgements.
    re.compile(r"\bshould\s+(i|we)\b(\s+\w+){0,4}\s+(buy|sell|hold|invest|load|get|add|exit|enter|ape|short|long|"
               r"dump|keep|accumulate|bet|stake|move|switch|rotate|wait)\w*\b", _F),
    re.compile(r"\b(do|would)\s+you\s+(think|reckon|believe|say)\b.{0,60}\b(should|buy|sell|hold|go\s+up|go\s+down|"
               r"rise|fall|pump|dump|worth|bottom|top|recover|moon)\w*\b", _F),
    re.compile(r"\bwhere\s+(do|would)\s+you\s+(see|expect|think)\b", _F),
    re.compile(r"\b(next|upcoming)\s+(\w+\s+){0,2}(coin|crypto|token|gem|\d+\s?x)\b", _F),
    re.compile(r"\b\d+\s?x\b.{0,30}\b(coin|crypto|token|gem|gains?|returns?)\b|\b(coin|crypto|token|gem)s?\b.{0,30}\b\d+\s?x\b", _F),
    re.compile(r"\b(undervalued|overvalued|cheap|expensive|bottomed|topped)\b.{0,40}\?|\b(has|is)\s+\w+\s+(bottomed|topped)\b", _F),
    re.compile(r"\bload(ing)?\s+up\s+on\b|\bstack(ing)?\s+(more\s+)?sats\b", _F),
    re.compile(r"\b(bullish|bearish)\s+or\s+(bullish|bearish)\b|\b(bullish|bearish)\b.{0,25}\b(next|tomorrow|this\s+week|"
               r"this\s+month|this\s+year)\b", _F),
    re.compile(r"\b(realistic|reasonable|your)\s+(price\s+)?(target|level|prediction|outlook)\b|\btargets?\s+for\s+\w+", _F),
    re.compile(r"\b(trade|trading|investment)\s+(recommendation|idea|signal|call|tip|setup)s?\b", _F),
    re.compile(r"\b(give|need|want|get)\s+(me\s+)?(some\s+)?(financial|investment|trading)\s+(advice|tips?|recommendations?)\b", _F),
    re.compile(r"\b(when|where)\s+(should|do|to)\s+(i\s+)?(buy|sell|enter|exit|take\s+profits?|set\s+(a\s+)?stop)\b", _F),
    re.compile(r"\b(buy|sell|hold)\s+(signal|recommendation|rating|call)s?\b", _F),
    re.compile(r"\b(all[- ]in|put\s+(all\s+)?my\s+(savings|money|life\s+savings|salary))\b", _F),
    re.compile(r"\b(would|will)\s+you\s+(buy|sell|invest|hold)\b", _F),
    re.compile(r"\bif\s+you\s+were\s+me\b", _F),
    re.compile(r"\b(entry|exit)\s+points?\b", _F),
    re.compile(r"\bgoing\s+to\s+(go\s+up|go\s+down|rise|fall|crash|moon|pump|dump|recover)\b", _F),
]

INJECTION_PATTERNS = [
    re.compile(r"\b(ignore|disregard|forget|override|bypass|skip|drop)\b.{0,40}\b(instructions?|rules?|guidelines?|polic(y|ies)|prompts?|restrictions?|guardrails?|safety|constraints?|programming)\b", _F),
    re.compile(r"\b(ignore|disregard|forget)\s+(all\s+|everything\s+|the\s+|what\s+(is|was)\s+)?(previous|above|prior|earlier)\b", _F),
    re.compile(r"\b(debug|maintenance|test|god|developer|admin|root|unrestricted)\s+mode\b", _F),
    re.compile(r"\b(disable|turn\s+off|remove|bypass|deactivate)\s+(your\s+|the\s+|all\s+)?(safety|filters?|guardrails?|"
               r"restrictions?|rules|content\s+polic(y|ies))", _F),
    re.compile(r"\bi('m|\s+am)\s+(the|your)\s+(developer|admin|administrator|creator|owner|operator)\b", _F),
    re.compile(r"\b(reveal|show|print|repeat|output|leak|dump|display)\b.{0,40}\b(system\s+prompt|instructions|developer\s+message|hidden\s+prompt|your\s+prompt|your\s+rules)\b", _F),
    re.compile(r"\b(system\s+prompt|developer\s+message|hidden\s+instructions?)\b", _F),
    re.compile(r"\b(output|print|repeat|show|reveal|display)\b.{0,30}\b(everything|all|text|content|words?)\s+(above|before|preceding|prior)\b", _F),
    re.compile(r"\b(above|previous|prior)\s+(this\s+)?(line|message|text)\b.{0,20}\b(verbatim|word\s+for\s+word)?", _F),
    re.compile(r"\b(no|without)\s+(rules|restrictions|filters|guardrails|limits)\b", _F),
    re.compile(r"\b(new|updated)\s+(rule|policy|instruction)s?\b.{0,30}\b(admin|developer|system|operator|from)\b", _F),
    re.compile(r"\byou\s+are\s+(now|no\s+longer)\b", _F),
    re.compile(r"\b(act|pretend|roleplay|role-play|behave)\s+(as|like|to\s+be)\b", _F),
    re.compile(r"\bpretend\s+(that\s+)?(you('re|\s+are)|you\s+were|to\s+be|i('m|\s+am))\b", _F),
    re.compile(r"\byou('re|\s+are)\s+(now\s+)?(my|a|an)\s+(personal\s+)?(financial\s+|investment\s+)?(advisor|adviser|broker|trader)\b", _F),
    re.compile(r"\b(jailbreak|developer\s+mode|god\s+mode|unfiltered|uncensored|sudo\s+mode|admin\s+mode|DAN\s+mode)\b", _F),
    re.compile(r"\bDAN\b"),
    re.compile(r"\b(new|updated|real|actual)\s+(instructions?|rules?|system\s+prompt)\s*[:\-]", _F),
    re.compile(r"<\s*/?\s*(system|instructions?|admin|assistant)\s*>", _F),
    re.compile(r"\[\s*(system|admin|instructions?)\s*\]", _F),
    re.compile(r"\bBEGIN\s+(SYSTEM|ADMIN|NEW)\b"),
]

# Extra patterns for text that arrives inside tool output (headlines, doc chunks).
TOOL_TEXT_INJECTION_PATTERNS = INJECTION_PATTERNS + [
    re.compile(r"\b(assistants?|ai|chatbots?|models?|agents?|llms?|bots?)\b.{0,40}\b(must|should|need\s+to|are\s+instructed\s+to|will\s+now)\b", _F),
    re.compile(r"\btell\s+(the\s+)?(users?|readers?|people|everyone|investors?)\s+to\b", _F),
    re.compile(r"\b(send|transfer|deposit)\b.{0,40}\b(to|into)\s+(this|the\s+following|our)\s+(wallet|address)\b", _F),
    re.compile(r"\b(recommend|urge|instruct)\w*\s+(users?|readers?|everyone|investors?)\s+to\s+(buy|sell)\b", _F),
]

REFUSAL_MESSAGES: dict[Category, str] = {
    "advice": (
        "I can't tell you whether to buy, sell or hold, and I don't make price predictions. I'm a market "
        "research tool, not a licensed financial advisor, and short-term prices can't be predicted reliably."
    ),
    "trade": (
        "I can't place trades or access any account. TickerSage is read-only by design: it has no order, "
        "wallet or account tools at all, so nothing here could execute a trade even if asked."
    ),
    "injection": (
        "I can't follow instructions that try to change how I work. I answer questions about crypto markets "
        "using public exchange data, and my rules stay the same."
    ),
    "off_topic": (
        "I only cover crypto markets: prices, volume, volatility, order books, news sentiment and crypto "
        "concepts."
    ),
}

FOLLOW_UPS: dict[Category, str] = {
    "advice": "I can show you the price, 24h move, volatility and recent news sentiment for any listed asset.",
    "trade": "If it helps, I can show you current market data for the asset instead.",
    "injection": "Try asking something like \"What's BTC trading at?\" or \"What is a funding rate?\"",
    "off_topic": "Try asking \"Compare ETH and SOL over the last 7 days\" or \"What is a funding rate?\"",
}


@dataclass
class GuardResult:
    allowed: bool
    category: Category | None = None
    reason: str | None = None
    matched: list[str] = field(default_factory=list)


def _first_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for p in patterns:
        m = p.search(text)
        if m:
            return m.group(0)
    return None


def check_input(question: str) -> GuardResult:
    text = " ".join(question.split())
    hit = _first_match(INJECTION_PATTERNS, text)
    if hit:
        return GuardResult(False, "injection", f"prompt injection pattern: {hit!r}", [hit])

    concept = bool(_CONCEPT_QUESTION.search(text)) and not _ACT_FOR_ME.search(text)
    if not concept:
        hit = _first_match(TRADE_PATTERNS, text) or _first_match(ACCOUNT_PATTERNS, text)
        if hit:
            return GuardResult(False, "trade", f"trade/account request: {hit!r}", [hit])
    elif _ACT_FOR_ME.search(text):
        return GuardResult(False, "trade", "asked to act on the user's behalf", [])

    hit = _first_match(ADVICE_PATTERNS, text)
    if hit and not _is_concept_definition(text, hit):
        return GuardResult(False, "advice", f"advice/prediction request: {hit!r}", [hit])
    return GuardResult(True)


def _is_concept_definition(text: str, hit: str) -> bool:
    """'What is a price target?' asks for a definition, not a prediction."""
    definition = re.match(
        r"^\s*(what|what's|whats)\s+(is|are|does)\s+(a|an|the)?\s*" + re.escape(hit.split()[0]), text, _F
    ) or re.match(r"^\s*(explain|define|describe)\b", text, _F)
    asks_for_call = re.search(r"\b(should\s+(i|we)|will\s+\w+\s+(go|rise|fall|reach|hit))\b", text, _F)
    return bool(definition) and not asks_for_call


def detect_injection(text: str) -> str | None:
    """Return the matching span when untrusted tool text contains instructions."""
    return _first_match(TOOL_TEXT_INJECTION_PATTERNS, " ".join(text.split()))


# ------------------------------------------------------------------ output

ADVICE_OUTPUT_PATTERNS = [
    re.compile(r"\byou\s+(should|could|might\s+want\s+to|may\s+want\s+to)\s+(consider\s+)?(buy|sell|hold|invest|short|long|accumulate|take\s+profit)\w*", _F),
    re.compile(r"\b(I|we)\s+(would\s+)?(recommend|suggest|advise)\s+(buying|selling|holding|investing|shorting|that\s+you)", _F),
    re.compile(r"\b(good|great|perfect|ideal|excellent)\s+(time|opportunity|entry|moment)\s+to\s+(buy|sell|enter|exit)\b", _F),
    re.compile(r"\b(strong\s+)?(buy|sell)\s+signal\b", _F),
    re.compile(r"\b(will|is\s+going\s+to|is\s+likely\s+to|is\s+expected\s+to|should)\s+(likely\s+)?(rise|fall|go\s+up|go\s+down|reach|hit|rally|crash|pump|dump|recover|bounce|moon)\b", _F),
    re.compile(r"\bguarantee", _F),
    re.compile(r"\bprice\s+target\b", _F),
    re.compile(r"\bbuy\s+the\s+dip\b", _F),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z*_\-\[(])")


@dataclass
class OutputCheck:
    text: str
    removed: list[str] = field(default_factory=list)


def check_output(answer: str) -> OutputCheck:
    """Drop sentences that read like advice or predictions."""
    kept_lines: list[str] = []
    removed: list[str] = []
    for line in answer.split("\n"):
        parts = _SENTENCE_SPLIT.split(line)
        kept = []
        for sentence in parts:
            if _first_match(ADVICE_OUTPUT_PATTERNS, sentence):
                removed.append(sentence.strip())
            else:
                kept.append(sentence)
        kept_lines.append(" ".join(kept))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()
    if removed:
        log.info("output guard removed %d sentence(s)", len(removed))
    return OutputCheck(text=text, removed=removed)


def data_footer(as_of: datetime | None, sources: list[str]) -> str:
    if as_of is None:
        return "_Market research only. This is not investment advice._"
    src = ", ".join(dict.fromkeys(sources)) or "public exchange API"
    return f"_Data as of {as_of:%H:%M} UTC on {as_of:%Y-%m-%d} from {src}. Market data only, not investment advice._"


def log_refusal(request_id: str, category: str, reason: str | None) -> None:
    log.info("refusal request_id=%s category=%s reason=%s", request_id, category, reason)
