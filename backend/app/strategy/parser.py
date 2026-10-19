"""Deterministic parser for common plain-language strategy phrasings.

It runs offline, so it is both the compiler when no LLM is configured and the fallback
when the LLM call fails. It never fills a gap with a guess: anything it can't read with
confidence is left empty or recorded as an issue, and the compiler asks about it.

Fixed readings (each one is reported in `notes`):
- "drops below" / "rises above" / "crosses" are crosses; "is below" / "under" / "<" are levels.
- "oversold" is RSI below 30 and "overbought" RSI above 70 (the thresholds compute_metrics reports).
- A golden cross is SMA(50) crossing above SMA(200); a death cross is the reverse.
- An RSI with no period is RSI(14); missing fees and slippage use 0.1% and 0.05% per fill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from ..symbols import SymbolRegistry
from .spec import (
    DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE_PCT, INTERVAL_HOURS, RSI_DEFAULT_PERIOD, PriceChangeCondition,
    PriceSmaCondition, RsiCondition, SmaCrossCondition, StrategyDraft,
)

_F = re.I
NUM = r"(?:\d+(?:\.\d+)?|\.\d+)"
UNIT = r"(?:minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|wks?|w|candles?|bars?)"
UNIT_HOURS = {"m": 1 / 60, "h": 1.0, "d": 24.0, "w": 168.0}

_WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "ten": "10", "twelve": "12", "fifteen": "15", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "hundred": "100",
}

# Strategy jargon that SymbolRegistry.extract would otherwise treat as an unknown ticker.
_NOT_ASSETS = {"TP", "SL", "SMA", "EMA", "RSI", "MACD", "ATR", "BB", "PNL", "DD", "ROI", "WHEN", "THEN", "STOP",
               "LOSS", "PROFIT", "TAKE", "EXIT", "ENTRY", "CANDLE", "CANDLES", "CHART", "DAILY", "HOURLY", "LONG",
               "SHORT", "MAX", "HOLD", "SIZE", "FEE", "FEES", "BELOW", "ABOVE", "CROSS", "CROSSES", "PRICE", "DROP",
               "DROPS", "RISES", "AFTER", "DAYS", "HOURS", "WEEK", "USE", "WITH", "AT", "ALL", "BALANCE", "DEMO"}


@dataclass
class Parsed:
    draft: StrategyDraft
    no_stop_loss: bool = False  # the text says to run without a stop
    issues: dict[str, str] = field(default_factory=dict)  # field -> why it can't be read yet
    unsupported: list[str] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)  # unsupported feature key -> the field it blocked
    notes: list[str] = field(default_factory=list)
    symbols_seen: list[str] = field(default_factory=list)
    pending_exit: bool = False  # an exit rule was mentioned but needs the interval to be read


# ------------------------------------------------------------------ helpers


def normalize(text: str) -> str:
    t = " ".join(text.replace("−", "-").replace("–", "-").replace("—", "-").split())
    t = re.sub(r"(\d)\s*(?:percent|per cent|pct)\b", r"\1%", t, flags=_F)
    t = re.sub(r"(\d),(\d{3})\b", r"\1\2", t)  # 10,000 -> 10000
    words = "|".join(_WORD_NUMBERS)
    t = re.sub(rf"\b({words})\b(?=\s*-?\s*(?:%|percent|{UNIT}\b))",
               lambda m: _WORD_NUMBERS[m.group(1).lower()], t, flags=_F)
    t = re.sub(rf"\b(below|above|under|over|than)\s+({words})\b",
               lambda m: f"{m.group(1)} {_WORD_NUMBERS[m.group(2).lower()]}", t, flags=_F)
    return t


def numbers_in(text: str) -> list[float]:
    """Every number written in the text (after normalising), for grounding and the decimal check."""
    return [float(n) for n in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?|\.\d+)", normalize(text))]


def _num(s: str) -> float:
    return float(s)


def _unit_key(unit: str) -> str:
    u = unit.lower()
    if u.startswith(("candle", "bar")):
        return "bar"
    if u.startswith("mi") or u == "m":
        return "m"
    return u[0]


def _duration(n: str | None, unit: str) -> tuple[float, str]:
    """('3', 'days') -> (3.0, 'd'); 'a day' -> (1.0, 'd'); candles stay as bars until the interval is known."""
    count = 1.0 if n is None or n.lower() in ("a", "an", "one") else _num(n)
    return count, _unit_key(unit)


_DURATION_ANY = re.compile(rf"(?:(?P<n>{NUM}|a|an|one)\s*-?\s*)?(?P<unit>{UNIT})\b", _F)


def parse_duration(text: str) -> tuple[float, str] | None:
    """An answer like '24 hours', 'a day' or '6 candles'."""
    m = _DURATION_ANY.search(normalize(text))
    return _duration(m.group("n"), m.group("unit")) if m else None


def _to_hours(count: float, key: str, interval: str | None) -> float | None:
    if key == "bar":
        return None if interval is None else count * INTERVAL_HOURS[interval]
    return count * UNIT_HOURS[key]


def _blank(text: str, m: re.Match[str]) -> str:
    """Remove a matched span but keep every other index in place."""
    return text[: m.start()] + " " * (m.end() - m.start()) + text[m.end():]


# ------------------------------------------------------------------ intervals

_IV_COMPACT = {"15m": "15m", "1h": "1h", "60m": "1h", "4h": "4h", "240m": "4h", "1d": "1d", "24h": "1d",
               "m15": "15m", "h1": "1h", "h4": "4h", "d1": "1d"}
_CHART_WORDS = r"(?:charts?|candles?|candlesticks?|timeframes?|time\s*frames?|bars?|tf|klines?)"
_INTERVAL_RES: list[tuple[str, re.Pattern[str]]] = [
    ("15m", re.compile(rf"\b(?:15|fifteen)\s*-?\s*min(?:ute)?s?\s+{_CHART_WORDS}", _F)),
    ("1h", re.compile(rf"\b(?:1|one)\s*-?\s*h(?:ou)?rs?\s+{_CHART_WORDS}|\b(?:1|one)\s*-?\s*hour\s+{_CHART_WORDS}"
                      rf"|\bhourly\b|\b60\s*-?\s*min(?:ute)?s?\s+{_CHART_WORDS}", _F)),
    ("4h", re.compile(rf"\b(?:4|four)\s*-?\s*h(?:ou)?rs?\s+{_CHART_WORDS}|\b(?:4|four)\s*-?\s*hour(?:ly)?\b(?!\s*s\b)"
                      rf"(?=\s+{_CHART_WORDS}|\s*$|ly)", _F)),
    ("1d", re.compile(rf"\bdaily\b|\b(?:1|one)\s*-?\s*day\s+{_CHART_WORDS}|\bday\s+{_CHART_WORDS}|"
                      r"\bthe\s+daily\b", _F)),
]
# Compact tokens ("4h", "1D") are a timeframe unless they read as a duration ("exit after 4h", "in 1d").
_IV_TOKEN = re.compile(r"(?<![\w.])(15m|1h|60m|4h|240m|1d|24h|m15|h1|h4|d1)(?![\w%])", _F)
_DURATION_LEAD = re.compile(r"(?:after|within|in|for|over|past|last|of|hold|holding|max|maximum|than|by|to)\s*$", _F)


def detect_intervals(text: str) -> list[str]:
    """Candle timeframes the text names, in order of appearance, without duplicates."""
    t = normalize(text)
    found: list[tuple[int, str]] = []
    for interval, pattern in _INTERVAL_RES:
        for m in pattern.finditer(t):
            if interval == "1d" and re.search(r"\b\d+\s*-?\s*$", t[: m.start()]):
                continue  # "200 daily"? leave it to the period parser
            found.append((m.start(), interval))
    for m in _IV_TOKEN.finditer(t):
        token = m.group(1).lower()
        if token == "24h" and not re.match(rf"\s+{_CHART_WORDS}", t[m.end():], _F):
            continue  # "in 24h" is a duration
        if _DURATION_LEAD.search(t[: m.start()]) and not re.match(rf"\s+{_CHART_WORDS}", t[m.end():], _F):
            continue
        found.append((m.start(), _IV_COMPACT[token]))
    out: list[str] = []
    for _, iv in sorted(found):
        if iv not in out:
            out.append(iv)
    return out


# ------------------------------------------------------------------ scalars

_BALANCE = (r"(?:of\s+(?:the\s+|my\s+|our\s+|a\s+)?(?:demo\s+|paper\s+|total\s+|available\s+|notional\s+)?"
            r"(?:balance|account|portfolio|capital|equity|funds|bankroll|stack|cash|money))")
_NOT_SIZE_AFTER = (r"(?!\s*(?:stop|sl\b|take|tp\b|profit|target|trailing|loss|gain|drop|rise|fall|move|fee|slippage|"
                   r"below|above))")

_FEE_RES = [
    re.compile(rf"(?:trading\s+)?(?:fees?|commissions?)\s*(?:of|:|=|at|is|are)?\s*(?P<v>{NUM})\s*%", _F),
    re.compile(rf"(?P<v>{NUM})\s*%\s*(?:trading\s+)?(?:fees?|commissions?)", _F),
    re.compile(r"\b(?:no|zero|without(?:\s+any)?)\s+(?:trading\s+)?(?:fees?|commissions?)\b", _F),
]
_SLIP_RES = [
    re.compile(rf"slippage\s*(?:of|:|=|at|is)?\s*(?P<v>{NUM})\s*%", _F),
    re.compile(rf"(?P<v>{NUM})\s*%\s*(?:of\s+)?slippage", _F),
    re.compile(r"\b(?:no|zero|without(?:\s+any)?)\s+slippage\b", _F),
]
_SIZE_RES = [
    re.compile(rf"(?:position[- ]?siz(?:e|ing)|size|sizing|allocation|allocate|use|using|put|invest|spend|bet|with|"
               rf"stake|deploy|buy\s+with|trade\s+with)\s*(?:of|:|=|is|at|with)?\s*(?P<v>{NUM})\s*%{_NOT_SIZE_AFTER}"
               rf"(?:\s*{_BALANCE})?", _F),
    re.compile(rf"(?<!risk )(?<!risking )(?P<v>{NUM})\s*%\s*(?:{_BALANCE}|(?:per|each|a)\s+(?:trade|position)|"
               rf"(?:position|trade)\s+size|size\b|allocation|position\b)", _F),
    re.compile(rf"\$\s?(?P<amt>{NUM})\s*(?P<k>k)?\s*(?:per\s+trade|a\s+trade|each\s+trade|per\s+position|position|"
               rf"of\s+the\s+demo|of\s+(?:my|the)\s+(?:demo\s+)?balance)", _F),
    re.compile(r"\b(?:all[- ]in|(?:the\s+|my\s+)?(?:whole|entire|full)\s+(?:demo\s+)?(?:balance|account|portfolio|"
               r"capital|stack))\b", _F),
    re.compile(rf"position[- ]?siz(?:e|ing)\s*:?\s*\$\s?(?P<amt>{NUM})\s*(?P<k>k)?", _F),
]
_TP_RES = [
    re.compile(rf"(?:take[- ]?profits?|tp|profit[- ]?target|target|take\s+(?:a\s+)?profit|lock\s+in|aim\s+for)\s*"
               rf"(?:at|of|:|=|is|on|when\s+up|once\s+up|if\s+up|around)?\s*(?:a\s+|\+\s*)?(?P<v>{NUM})\s*%", _F),
    re.compile(rf"(?:sell|exit|close|get\s+out)\s+(?:the\s+(?:trade|position)\s+)?(?:at|on|with|for)\s+(?:a\s+)?\+\s*"
               rf"(?P<v>{NUM})\s*%", _F),
    re.compile(rf"(?:sell|exit|close|get\s+out)\s+(?:the\s+(?:trade|position)\s+)?(?:at|on|with|for)\s+(?:a\s+)?"
               rf"(?P<v>{NUM})\s*%\s*(?:gain|profit|up)\b", _F),
    re.compile(rf"(?:sell|exit|close|get\s+out)\s+(?:when|once|if)\s+(?:it'?s\s+|it\s+is\s+|price\s+is\s+|i'?m\s+|"
               rf"we'?re\s+)?up\s+(?P<v>{NUM})\s*%", _F),
    re.compile(rf"take\s+(?P<v>{NUM})\s*%\s*(?:profits?|gains?)", _F),
    re.compile(rf"(?P<v>{NUM})\s*%\s*(?:take[- ]?profit|tp\b|profit[- ]?target|target|profit|gain)", _F),
    re.compile(rf"(?:rises?|gains?|is\s+up|goes\s+up|climbs?|up)\s+(?P<v>{NUM})\s*%\s+(?:from|above|over)\s+"
               rf"(?:the\s+|my\s+|our\s+)?(?:entry|buy|purchase|cost|fill)", _F),
]
_SL_RES = [
    re.compile(rf"(?:stop[- ]?loss(?:es)?|stop(?:[- ]?out)?|sl|cut\s+(?:my\s+|the\s+)?loss(?:es)?(?:\s+short)?|"
               rf"max(?:imum)?\s+loss)\s*(?:at|of|:|=|is|on|below\s+entry|under\s+entry)?\s*(?:a\s+|of\s+)?-?\s*"
               rf"(?P<v>{NUM})\s*%", _F),
    re.compile(rf"(?P<v>{NUM})\s*%\s*(?:trailing\s+)?(?:stop(?:[- ]?loss)?|sl\b|max(?:imum)?\s+loss|loss\s+limit|"
               rf"loss\b(?!\s+(?:in|within|over)))", _F),
    re.compile(rf"(?:sell|exit|close|get\s+out|bail(?:\s+out)?)\s+(?:the\s+(?:trade|position)\s+)?(?:at|on|if|when)\s+"
               rf"(?:a\s+)?-\s*(?P<v>{NUM})\s*%", _F),
    re.compile(rf"(?:sell|exit|close|get\s+out|bail(?:\s+out)?)\s+(?:the\s+(?:trade|position)\s+)?(?:at|on|if|when|"
               rf"after)\s+(?:a\s+)?(?P<v>{NUM})\s*%\s*(?:loss|drawdown|down|drop)\b", _F),
    re.compile(rf"(?:falls?|drops?|is\s+down|goes\s+down|loses?|declines?|dips?)\s+(?P<v>{NUM})\s*%\s+(?:from|below|"
               rf"under)\s+(?:the\s+|my\s+|our\s+)?(?:entry|buy|purchase|cost|fill)", _F),
]
_NO_STOP = re.compile(r"\b(?:no|without(?:\s+a)?|zero|skip(?:\s+the)?|don'?t\s+use\s+a|never\s+use\s+a)\s+"
                      r"stop(?:[- ]?loss)?s?\b|\bstop(?:[- ]?loss)?\s*:?\s*(?:none|no|n/?a|nothing)\b", _F)
_BARE_TP = re.compile(rf"(?:take[- ]?profit|tp|profit[- ]?target)\s*(?:at|of|:|=)?\s*\$?(?P<v>{NUM})(?![\d.%])", _F)
_BARE_SL = re.compile(rf"(?:stop[- ]?loss|stop|sl)\s*(?:at|of|:|=)?\s*\$?(?P<v>{NUM})(?![\d.%])(?!\s*{UNIT}\b)", _F)
_HOLD_RES = [
    re.compile(rf"(?:exit|close|sell|get\s+out|hold|holding|out)\s+(?:the\s+(?:trade|position)\s+|it\s+)?"
               rf"(?:after|for\s+(?:at\s+most|max(?:imum)?|up\s+to|no\s+more\s+than)?|within|at\s+most|no\s+later\s+than|"
               rf"no\s+longer\s+than|if\s+(?:still\s+open|not\s+closed)\s+after)\s+(?P<n>{NUM}|a|an|one)\s*-?\s*"
               rf"(?P<unit>{UNIT})\b", _F),
    re.compile(rf"max(?:imum)?\s*(?:hold(?:ing)?\s*(?:time|period|duration)?|holding|duration|time\s+in\s+(?:the\s+)?"
               rf"trade|trade\s+duration)\s*(?:of|:|=|is)?\s*(?P<n>{NUM}|a|an|one)\s*-?\s*(?P<unit>{UNIT})\b", _F),
    re.compile(rf"time[- ]?(?:stop|exit|limit)\s*(?:of|:|=|at|after)?\s*(?P<n>{NUM}|a|an|one)\s*-?\s*(?P<unit>{UNIT})\b",
               _F),
    re.compile(rf"(?P<n>{NUM}|a|an|one)\s*-?\s*(?P<unit>{UNIT})\s+(?:max(?:imum)?\s+)?(?:hold(?:ing)?\s*(?:time|period|"
               rf"limit)?|time[- ]?stop)\b", _F),
]


def _last(patterns: list[re.Pattern[str]], text: str) -> re.Match[str] | None:
    """The last match of any pattern: answers are appended to the idea, so a later value wins."""
    best: re.Match[str] | None = None
    for p in patterns:
        for m in p.finditer(text):
            if best is None or m.start() > best.start():
                best = m
    return best


# ------------------------------------------------------------------ conditions

_SEG_SPLIT = re.compile(
    r"[;\n!?]|[.,](?!\d)|\bthen\b|\bbut\b|\b(?:and|&)\s+(?=(?:then\s+)?(?:sell|exit|close|take|stop|cut|buy|enter|"
    r"go\s+long|get\s+out|hold|use|risk|size|with|max)\b)", _F)
_EXIT_CUE = re.compile(r"\b(sell|exit|close|get\s+out|take[- ]?profit|tp|stop|sl|cut|bail|dump\s+it|out\s+when)\b", _F)
_ENTRY_CUE = re.compile(r"\b(buy|enter|entry|go\s+long|long|open|get\s+in|accumulate|purchase)\b", _F)

_RSI = re.compile(r"(?:(?P<pre>\d{1,3})\s*-?\s*(?:period|bar|candle|day|hour|h|d)?\s*-?\s*)?\brsi\b"
                  r"(?:\s*\(\s*(?P<paren>\d{1,3})\s*\)|\s*-\s*(?P<dash>\d{1,3})\b|\s+(?P<sp>\d{1,3})\b(?=\s+(?:is|drops?|"
                  r"falls?|goes|crosses|rises?|dips?|moves?|gets|reads|closes|stays|below|above|under|over|<|>)))?", _F)
_RSI_OPS: list[tuple[str, re.Pattern[str]]] = [
    ("crosses_below", re.compile(rf"(?:drops?|falls?|dips?|goes|moves?|cross(?:es)?|breaks?|slips?|sinks?|gets|comes)"
                                 rf"\s+(?:back\s+|down\s+)?(?:below|under|beneath|through)\s+(?:the\s+)?(?:level\s+of\s+)?"
                                 rf"(?P<v>-?{NUM})", _F)),
    ("crosses_above", re.compile(rf"(?:rises?|climbs?|goes|moves?|cross(?:es)?|breaks?|gets|jumps?|pops?|comes|pushes)"
                                 rf"\s+(?:back\s+|up\s+)?(?:above|over|past|through)\s+(?:the\s+)?(?:level\s+of\s+)?"
                                 rf"(?P<v>-?{NUM})", _F)),
    ("below", re.compile(rf"(?:below|under|beneath|less\s+than|lower\s+than|<=?|≤)\s*(?P<v>-?{NUM})", _F)),
    ("above", re.compile(rf"(?:above|over|greater\s+than|higher\s+than|more\s+than|>=?|≥|exceeds?)\s*"
                         rf"(?P<v>-?{NUM})", _F)),
    ("reaches", re.compile(rf"(?:hits?|reach(?:es)?|touch(?:es)?|tags?|gets\s+to)\s+(?P<v>-?{NUM})", _F)),
    ("oversold", re.compile(r"\boversold\b", _F)),
    ("overbought", re.compile(r"\boverbought\b", _F)),
]
_MA_WORD = r"(?:simple\s+moving\s+average|moving\s+average|moving\s+avg|sma|ma)\b"
_MA_UNIT = r"(?:\s*-?\s*(?:day|period|bar|candle|hour|h|d|week|w|minute|min|m))?"
_MA_REF = re.compile(rf"(?:\b(?:sma|ma)\s*(?:\(\s*(?P<a>\d{{1,4}})\s*\)|-?\s*(?P<b>\d{{1,4}})\b)|\b(?P<c>\d{{1,4}}){_MA_UNIT}"
                     rf"\s*-?\s*{_MA_WORD})", _F)
_CROSS_VERB = re.compile(r"(?:cross(?:es|ing)?|breaks?|moves?|goes|rises?|climbs?|drops?|falls?|dips?|pops?|jumps?|"
                         r"slips?|comes)\s+(?:back\s+)?(?:up\s+|down\s+)?(?:above|over|below|under|through)\b", _F)
_LEVEL_DIR = re.compile(r"(above|over|below|under)\s+(?:the\s+|its\s+|a\s+|an\s+)?$", _F)
_ANY_DIR = re.compile(r"(above|over|below|under|through)\s+(?:the\s+|its\s+|a\s+|an\s+)?$", _F)
_GOLDEN = re.compile(r"\b(golden|death)\s+cross\b", _F)
_SLASH_CROSS = re.compile(rf"\b(?P<f>\d{{1,4}})\s*/\s*(?P<s>\d{{1,4}})\s*(?:{_MA_WORD})?\s*"
                          r"(?:cross(?:over)?|crossing)\b", _F)
_PCHG = re.compile(
    rf"(?:(?P<verb>drops?|dropped|falls?|fell|dips?|declines?|is\s+down|goes\s+down|dumps?|sinks?|crashes|crash|"
    rf"loses?|slides?|tanks?|down|rises?|rose|gains?|is\s+up|goes\s+up|jumps?|pumps?|climbs?|rallies|rally|surges?|"
    rf"spikes?|up)\s+(?:by\s+)?(?:more\s+than\s+|at\s+least\s+|over\s+|>\s*)?(?P<pct>{NUM})\s*%"
    rf"|(?P<pct2>{NUM})\s*%\s+(?P<noun>drop|fall|decline|dip|dump|crash|sell-?off|rise|gain|pump|jump|rally|move\s+up|"
    rf"move\s+down|surge))(?P<tail>[^,;]{{0,48}})", _F)
_DOWN_WORDS = re.compile(r"drop|fell|fall|dip|declin|down|dump|sink|crash|los|slid|tank|sell", _F)
_LOOKBACK = re.compile(rf"(?:in|within|over|during|across|on)\s+(?:the\s+|a\s+single\s+)?(?:last\s+|past\s+|previous\s+|"
                       rf"prior\s+|same\s+)?(?P<n>{NUM}|a|an|one)?\s*-?\s*(?P<unit>{UNIT})\b", _F)
_PRE_LOOKBACK = re.compile(rf"(?P<n>{NUM}|a|an|one)\s*-?\s*(?P<unit>hour|day|week|candle|bar|minute|h|d)\s*$", _F)
_LOOKBACK_ANSWER = re.compile(rf"price\s+change\s+window\s*:\s*(?P<n>{NUM}|a|an|one)?\s*-?\s*(?P<unit>{UNIT})\b", _F)

# key -> (field the question goes to, message, pattern, read the whole text or only what scalars left)
_UNSUPPORTED: dict[str, tuple[str, str, re.Pattern[str], bool]] = {
    "short": (
        "entry", "Short selling isn't supported: Strategy Studio backtests long (buy first) rules only.",
        re.compile(r"\b(?:go(?:ing)?|open(?:ing)?\s+a|enter(?:ing)?\s+a|take\s+a)\s+short\b|\bshort(?:ing)?\s+(?:it|"
                   r"[A-Za-z]{2,6})\s+(?:when|if|on)\b|\bsell\s+short\b|\bshort[- ]sell|^\s*short\b", _F), True),
    "leverage": (
        "position_size",
        "Leverage and margin aren't supported: positions are spot and use at most 100% of the demo balance.",
        re.compile(r"\b\d+(?:\.\d+)?\s*x\s+(?:leverage|long|lev)\b|\bleverage[ds]?\b|\bon\s+margin\b|\bmargin\s+trad", _F),
        True),
    "trailing_stop": (
        "stop_loss", "Trailing stops aren't supported yet: use a fixed stop loss %.",
        re.compile(r"\btrailing\s+stop", _F), True),
    "ema": ("entry", "EMA isn't supported yet: use a simple moving average (SMA).",
            re.compile(r"\bemas?\b|exponential", _F), True),
    "macd": ("entry", "MACD isn't supported yet.", re.compile(r"\bmacd\b", _F), True),
    "bollinger": ("entry", "Bollinger Bands aren't supported yet.", re.compile(r"\bbollinger|\bbbands?\b", _F), True),
    "volume": ("entry", "Volume conditions aren't supported yet.",
               re.compile(r"\bvolume\s+(?:spike|above|below|is|>|<|over|under|rises|jumps|doubles)", _F), True),
    "other_indicator": (
        "entry", "Other indicators (VWAP, ATR, stochastic, Ichimoku) aren't supported yet.",
        re.compile(r"\b(?:vwap|atr|stoch(?:astic)?|ichimoku|supertrend|fibonacci|fib)\b", _F), True),
    "price_level": (
        "entry", "Price levels in dollars aren't supported: write conditions as indicators or % moves.",
        re.compile(r"(?:hits|reaches|touches|at|below|above|under|over|breaks|to)\s+\$\s?\d{2,}"
                   r"|(?:hits|reaches|touches|breaks|drops\s+to|falls\s+to|goes\s+to|is\s+at|trades\s+at|gets\s+to)\s+"
                   r"\d{3,}(?![\d%])|\$\s?\d{3,}(?![\d.,])(?!\s*(?:k\b|per\b|a\s+trade|each|position|of\b))", _F),
        False),
}
UNSUPPORTED_KEYS = tuple(_UNSUPPORTED)


def _seg_context(seg: str, previous: str) -> str:
    exit_m, entry_m = _EXIT_CUE.search(seg), _ENTRY_CUE.search(seg)
    if exit_m and (not entry_m or exit_m.start() < entry_m.start()):
        return "exit"
    if entry_m:
        return "entry"
    return previous


def _segments(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    start = 0
    for m in _SEG_SPLIT.finditer(text):
        out.append((start, text[start:m.start()]))
        start = m.end()
    out.append((start, text[start:]))
    return [(i, s) for i, s in out if s.strip()]


def _ma_period(m: re.Match[str]) -> int:
    return int(m.group("a") or m.group("b") or m.group("c"))


def _rsi_conditions(seg: str, notes: list[str]) -> list[tuple[int, object]]:
    out: list[tuple[int, object]] = []
    matches = list(_RSI.finditer(seg))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(seg)
        window = seg[m.end(): min(end, m.end() + 60)]
        period_s = m.group("paren") or m.group("dash") or m.group("sp") or m.group("pre")
        period = int(period_s) if period_s else RSI_DEFAULT_PERIOD
        best: tuple[int, str, re.Match[str]] | None = None
        for op, pattern in _RSI_OPS:
            hit = pattern.search(window)
            if hit and (best is None or hit.start() < best[0]):
                best = (hit.start(), op, hit)
        if best is None:
            continue
        _, op, hit = best
        if op == "oversold":
            op, value = "below", 30.0
            notes.append("'oversold' read as RSI below 30")
        elif op == "overbought":
            op, value = "above", 70.0
            notes.append("'overbought' read as RSI above 70")
        else:
            value = _num(hit.group("v"))
        if op == "reaches":
            if value == 50:
                continue
            op = "crosses_above" if value > 50 else "crosses_below"
            notes.append(f"'{hit.group(0)}' read as RSI crossing {op.split('_')[1]} {value:g}")
        if not period_s:
            notes.append(f"RSI period not given: using RSI({RSI_DEFAULT_PERIOD})")
        reaches = hit.group(0).lower().startswith(("hit", "reach", "touch", "tag", "gets to"))
        if op.startswith("crosses") and not reaches:
            notes.append(f"'{hit.group(0).split()[0]} {op.split('_')[1]}' read as RSI crossing {op.split('_')[1]} "
                         f"{value:g}, not just being {op.split('_')[1]} it")
        out.append((m.start(), RsiCondition(period=period, op=op, value=value)))  # type: ignore[arg-type]
    return out


def _sma_conditions(seg: str, notes: list[str]) -> tuple[list[tuple[int, object]], str]:
    """SMA crosses and price-vs-SMA conditions in one segment. Returns the conditions and the segment with
    their spans blanked, so their periods aren't read as anything else."""
    out: list[tuple[int, object]] = []
    for m in _GOLDEN.finditer(seg):
        golden = m.group(1).lower() == "golden"
        out.append((m.start(), SmaCrossCondition(fast=50, slow=200, op="crosses_above" if golden else "crosses_below")))
        notes.append(f"'{m.group(0)}' read as SMA(50) crossing {'above' if golden else 'below'} SMA(200)")
        seg = _blank(seg, m)
    for m in _SLASH_CROSS.finditer(seg):
        f, s = int(m.group("f")), int(m.group("s"))
        bearish = re.search(r"\b(bearish|down(?:ward)?|below|under)\b", seg[max(0, m.start() - 25): m.end() + 25], _F)
        out.append((m.start(), SmaCrossCondition(fast=f, slow=s, op="crosses_below" if bearish else "crosses_above")))
        seg = _blank(seg, m)
    refs = list(_MA_REF.finditer(seg))
    used: set[int] = set()
    for i in range(len(refs) - 1):
        a, b = refs[i], refs[i + 1]
        between = seg[a.end(): b.start()]
        verb = _CROSS_VERB.search(between)
        if verb and len(between) <= 40 and _ANY_DIR.search(between):
            direction = _ANY_DIR.search(between).group(1).lower()  # type: ignore[union-attr]
            if direction == "through":
                continue
            op = "crosses_above" if direction in ("above", "over") else "crosses_below"
            out.append((a.start(), SmaCrossCondition(fast=_ma_period(a), slow=_ma_period(b), op=op)))
            used.update((i, i + 1))
    for i, ref in enumerate(refs):
        if i in used:
            continue
        before = seg[max(0, ref.start() - 45): ref.start()]
        lead = _LEVEL_DIR.search(before)
        if not lead:
            continue
        direction = "above" if lead.group(1).lower() in ("above", "over") else "below"
        crossing = re.search(r"(?:cross(?:es|ing)?|breaks?|moves?|goes|rises?|climbs?|drops?|falls?|dips?|pops?|"
                             r"jumps?|slips?|comes)\s+(?:back\s+)?(?:up\s+|down\s+)?\w+\s+(?:the\s+|its\s+|a\s+|an\s+)?$",
                             before, _F)
        op = f"crosses_{direction}" if crossing else direction
        out.append((ref.start(), PriceSmaCondition(period=_ma_period(ref), op=op)))  # type: ignore[arg-type]
    for ref in refs:
        seg = _blank(seg, ref)
    return out, seg


def _price_changes(seg: str, interval: str | None, context: str, parsed: Parsed,
                   lookback_answer: tuple[float, str] | None) -> list[tuple[int, object]]:
    out: list[tuple[int, object]] = []
    for m in _PCHG.finditer(seg):
        word = m.group("verb") or m.group("noun") or ""
        pct = _num(m.group("pct") or m.group("pct2"))
        direction = "down" if _DOWN_WORDS.search(word) else "up"
        tail = m.group("tail") or ""
        if re.match(r"\s*(?:from|below|above|under|over)\s+(?:the\s+|my\s+|our\s+)?(?:entry|buy|purchase|cost|fill)",
                    tail, _F):
            continue  # "falls 3% from entry" is a stop loss, read by _SL_RES
        lb = _LOOKBACK.search(tail)
        pre = _PRE_LOOKBACK.search(seg[: m.start()]) if m.group("noun") else None
        duration = _duration(lb.group("n"), lb.group("unit")) if lb else \
            _duration(pre.group("n"), pre.group("unit")) if pre else None
        if duration is None and context == "exit":
            # "sell if it drops 3%" / "sell when it gains 5%": a move from the entry price.
            if direction == "down":
                parsed.draft.stop_loss_pct = parsed.draft.stop_loss_pct or pct
            else:
                parsed.draft.take_profit_pct = parsed.draft.take_profit_pct or pct
            parsed.notes.append(f"'{m.group(0).strip()}' after a sell read as a move from the entry price")
            continue
        if duration is None:
            duration = lookback_answer
        if duration is None:
            parsed.issues["lookback"] = (f"Over what period should the {pct:g}% "
                                         f"{'drop' if direction == 'down' else 'rise'} be measured?")
            continue
        hours = _to_hours(duration[0], duration[1], interval)
        if hours is None:
            parsed.pending_exit = parsed.pending_exit or context == "exit"
            continue  # counted in candles: readable once the interval is known
        out.append((m.start(), PriceChangeCondition(direction=direction, pct=pct, lookback_hours=hours)))
    return out


# ------------------------------------------------------------------ main entry point


def parse(text: str, registry: SymbolRegistry, *, interval: str | None = None,
          lookback: tuple[float, str] | None = None) -> Parsed:
    """Read `text`. `interval` and `lookback` come from answers to earlier questions and take precedence."""
    t = normalize(text)
    parsed = Parsed(draft=StrategyDraft())
    d = parsed.draft

    # Asset
    symbols = [s for s in registry.extract(t) if s.upper() not in _NOT_ASSETS]
    parsed.symbols_seen = symbols
    if len(symbols) == 1:
        d.symbol = symbols[0].upper()
    elif len(symbols) > 1:
        parsed.issues["symbol"] = (f"You mentioned {', '.join(symbols[:4])}. Strategy Studio trades one asset at a "
                                   "time: which one should the rules trade?")

    # Timeframe
    intervals = [interval] if interval else detect_intervals(t)
    if len(intervals) == 1:
        d.interval = intervals[0]  # type: ignore[assignment]
    elif len(intervals) > 1:
        parsed.issues["interval"] = (f"You mentioned {' and '.join(intervals)} candles. Which one should the rules "
                                     "run on?")
    interval = d.interval

    # Scalars, matched on the whole text; each span is blanked so its number isn't read twice.
    work = t
    if m := _last(_FEE_RES, work):
        d.fee_pct = _num(m.group("v")) if "v" in m.groupdict() and m.group("v") else 0.0
        work = _blank(work, m)
    else:
        parsed.notes.append(f"Fees not given: using {DEFAULT_FEE_PCT:g}% per fill (Binance spot taker)")
    if m := _last(_SLIP_RES, work):
        d.slippage_pct = _num(m.group("v")) if "v" in m.groupdict() and m.group("v") else 0.0
        work = _blank(work, m)
    else:
        parsed.notes.append(f"Slippage not given: using {DEFAULT_SLIPPAGE_PCT:g}% per fill")
    if m := _last(_SL_RES, work):
        d.stop_loss_pct = _num(m.group("v"))
        work = _blank(work, m)
    if m := _last(_TP_RES, work):
        d.take_profit_pct = _num(m.group("v"))
        work = _blank(work, m)
    if m := _last(_SIZE_RES, work):
        gd = m.groupdict()
        if gd.get("amt"):
            amount = _num(gd["amt"]) * (1000 if gd.get("k") else 1)
            d.position_size_pct = round(amount / d.demo_balance * 100, 4)
            parsed.notes.append(f"${amount:,.0f} per trade read as {d.position_size_pct:g}% of the "
                                f"${d.demo_balance:,.0f} demo balance")
        elif gd.get("v"):
            d.position_size_pct = _num(gd["v"])
        else:
            d.position_size_pct = 100.0
            parsed.notes.append(f"'{m.group(0)}' read as 100% of the demo balance")
        work = _blank(work, m)
    no_stop = _NO_STOP.search(work)
    if no_stop and (d.stop_loss_pct is None or no_stop.start() > (_last(_SL_RES, t) or no_stop).start()):
        parsed.no_stop_loss, d.stop_loss_pct = True, None
        work = _blank(work, no_stop)
    if d.stop_loss_pct is None and not parsed.no_stop_loss and (bare := _BARE_SL.search(work)):
        parsed.issues["stop_loss"] = (f"You wrote '{bare.group(0).strip()}' without a %. Where should the stop loss "
                                      "be, as a % below the entry price?")
        work = _blank(work, bare)
    if d.take_profit_pct is None and (bare := _BARE_TP.search(work)):
        parsed.issues["exit"] = (f"You wrote '{bare.group(0).strip()}' without a %. Where should the take profit be, "
                                 "as a % above the entry price?")
        work = _blank(work, bare)
    if m := _last(_HOLD_RES, work):
        count, key = _duration(m.group("n"), m.group("unit"))
        hours = _to_hours(count, key, interval)
        if hours is None:
            parsed.pending_exit = True
        else:
            d.max_hold_hours = hours
        work = _blank(work, m)

    lookback_answer = lookback
    if m := _LOOKBACK_ANSWER.search(work):
        lookback_answer = _duration(m.group("n"), m.group("unit"))
        work = _blank(work, m)

    # Conditions, read clause by clause: a clause after "sell"/"exit" holds exit signals.
    entries: list[tuple[int, object]] = []
    exits: list[tuple[int, object]] = []
    contexts: list[tuple[int, int, str]] = []
    context = "entry"
    for offset, seg in _segments(work):
        context = _seg_context(seg, context)
        contexts.append((offset, offset + len(seg), context))
        found: list[tuple[int, object]] = []
        sma, rest = _sma_conditions(seg, parsed.notes)
        found += sma
        found += _rsi_conditions(rest, parsed.notes)
        found += _price_changes(rest, interval, context, parsed, lookback_answer)
        if not found and context == "exit" and (entries or exits):
            found += _mirror(rest, entries[-1][1] if entries else exits[-1][1], parsed.notes)
        found.sort(key=lambda x: x[0])
        (exits if context == "exit" else entries).extend((offset + i, c) for i, c in found)

    d.entry = [c for _, c in sorted(entries, key=lambda x: x[0])][:4]  # type: ignore[misc]
    d.exit_signals = [c for _, c in sorted(exits, key=lambda x: x[0])][:4]  # type: ignore[misc]
    if len(d.entry) > 1 and re.search(r"\b(?:or|either)\b", work[entries[0][0]: entries[-1][0]], _F):
        d.entry_logic = "any"

    # An unsupported feature blocks the field it belongs to, so a short, an EMA or a trailing stop is never
    # quietly turned into a different rule.
    def context_at(pos: int) -> str:
        return next((c for a, b, c in contexts if a <= pos < b), "entry")

    for key, (field_name, msg, pattern, whole) in _UNSUPPORTED.items():
        hit = pattern.search(t if whole else work)
        if not hit:
            continue
        if field_name == "entry" and context_at(hit.start()) == "exit" and "Short" not in msg:
            field_name = "exit"
        block(parsed, key, field_name)

    parsed.notes = list(dict.fromkeys(parsed.notes))
    try:  # re-validate: the parser fills the model field by field
        parsed.draft = StrategyDraft.model_validate(d.model_dump())
    except ValidationError as exc:  # pragma: no cover - every value above is positive and typed
        parsed.issues["entry"] = f"Couldn't read the rules: {exc.errors()[0]['msg']}"
    return parsed


def block(parsed: Parsed, key: str, field_name: str | None = None) -> None:
    """Record an unsupported feature and clear the field it would have changed."""
    field_name = field_name or _UNSUPPORTED[key][0]
    msg = _UNSUPPORTED[key][1]
    parsed.blocked[key] = field_name
    if msg not in parsed.unsupported:
        parsed.unsupported.append(msg)
    parsed.issues[field_name] = msg
    d = parsed.draft
    if field_name == "entry":
        d.entry = []
    elif field_name == "exit":
        d.exit_signals = []
    elif field_name == "position_size":
        d.position_size_pct = None
    elif field_name == "stop_loss":
        d.stop_loss_pct = None


_BACK_CROSS = re.compile(r"\b(?:cross(?:es)?|breaks?|drops?|falls?|goes|moves?|comes|dips?|rises?|climbs?)\s+"
                         r"(?:back\s+)?(?:down\s+|up\s+)?(below|under|above|over)\b(?!\s*-?\d)", _F)


def _mirror(seg: str, last: object, notes: list[str]) -> list[tuple[int, object]]:
    """'... sell when it crosses back below': the same indicator as the last condition, crossing the other way."""
    m = _BACK_CROSS.search(seg)
    if not m:
        return []
    op = "crosses_above" if m.group(1).lower() in ("above", "over") else "crosses_below"
    if isinstance(last, SmaCrossCondition):
        cond: object = SmaCrossCondition(fast=last.fast, slow=last.slow, op=op)
    elif isinstance(last, PriceSmaCondition):
        cond = PriceSmaCondition(period=last.period, op=op)
    elif isinstance(last, RsiCondition):
        cond = RsiCondition(period=last.period, op=op, value=last.value)
    else:
        return []
    notes.append(f"'{m.group(0)}' read as the same indicator crossing {op.split('_')[1]}")
    return [(m.start(), cond)]
