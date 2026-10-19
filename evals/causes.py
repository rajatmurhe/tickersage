"""The "no unsupported cause" check for why-it-moved answers.

An answer fails when it names a driver category the attribute_move evidence pack didn't flag, e.g. "the
drop was driven by selling pressure in the order book" when `order_book` isn't in the pack's `drivers`.
A sentence names a category as a cause when it has one of that category's terms and a causal cue
("driven by", "due to", "behind", "weighed on"...). Sentences that rule something out ("not", "no",
"rather than", "no clear driver") are skipped, and quoted headlines are removed first, since quoting a
headline isn't the answer's own claim. It's a keyword check: it needs no model, and it can miss a cause
phrased in words it doesn't list.
"""

from __future__ import annotations

import re
from typing import Any

_F = re.I

CATEGORY_TERMS: dict[str, re.Pattern[str]] = {
    "market_wide": re.compile(
        r"market[- ]wide|\bbroad(er)? (crypto )?market|\b(whole|overall|wider|entire|general) (crypto )?market"
        r"|\bmarket[- ](driven|led|sell-?off|rally|move|slump|downturn|decline|weakness|strength)"
        r"|\b(crypto|the) market (fell|dropped|rose|rallied|slid|sold off)"
        r"|\b(tracked|tracking|followed|following|mirrored|mirroring|in line with|in step with) (btc|bitcoin)\b"
        r"|\b(btc|bitcoin)'s (move|drop|fall|decline|slide|rise|rally|gain|weakness|strength)"
        r"|\brisk[- ](off|on)\b|\bmacro", _F),
    "volume": re.compile(r"\bvolume|\btrading activity|\bheavy trading", _F),
    "order_book": re.compile(
        r"order[- ]?book|\bbids?\b|\basks?\b|\b(buy|sell)(ing)?[- ](pressure|side|walls?)|\b(bid|ask)[- ]side"
        r"|\bdepth\b|\bliquidity", _F),
    "news": re.compile(r"\bnews|\bheadlines?\b|\bsentiment|\bmedia coverage|\bannouncement", _F),
    "funding": re.compile(
        r"\bfunding|\bleverage|\bliquidations?\b|\bperpetuals?\b|\bperps?\b|\bopen interest"
        r"|\b(short|long) squeeze|\bderivatives", _F),
}

CAUSAL = re.compile(
    r"\b(driv\w*|caus\w*|due to|because|behind|explain\w*|attribut\w*|fuel\w*|push\w*|led\b|lead(s|ing)? to"
    r"|on the back of|reflect\w*|points? to|contribut\w*|accounts? for|accounted for|trigger\w*|spark\w*|amid"
    r"|weigh\w*|dragg?\w*|boost\w*|result(ed|s)? (of|from)|stems? from|why)\b", _F)

RULED_OUT = re.compile(
    r"\b(no|not|never|neither|nor|without|rather than|instead of|unlikely|unavailable)\b|n't\b", _F)

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def named_causes(answer: str, quoted: list[str] | None = None) -> set[str]:
    """Driver categories the answer names as a cause."""
    text = answer
    for q in sorted(quoted or [], key=len, reverse=True):
        if q:
            text = text.replace(q, " ")
    named: set[str] = set()
    for sentence in _SENTENCE.split(text):
        if not CAUSAL.search(sentence) or RULED_OUT.search(sentence):
            continue
        named |= {name for name, pattern in CATEGORY_TERMS.items() if pattern.search(sentence)}
    return named


def unsupported_causes(answer: str, pack: dict[str, Any], quoted: list[str] | None = None) -> list[str]:
    """Categories the answer names as a cause that the pack didn't flag, in a stable order."""
    flagged = set(pack.get("drivers") or [])
    return sorted(named_causes(answer, quoted) - flagged)
