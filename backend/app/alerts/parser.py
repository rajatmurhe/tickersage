"""The deterministic alert parser: the offline compiler, the LLM path's fallback, and the source of the checks
that run on the LLM's output too (asset count, unsupported features, which intervals the text names).

It reads one asset and up to three conditions: RSI, price vs an SMA, SMA crosses, % moves over a window,
price levels and headline sentiment, plus an optional cooldown. Anything it can't read becomes a clarifying
question, never a guess. The RSI, SMA and duration patterns are the Strategy Studio parser's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..strategy.parser import (
    _LOOKBACK, _MA_REF, _PRE_LOOKBACK, _RSI_OPS, NUM, UNIT, _duration, _sma_conditions, _to_hours,
    detect_intervals, normalize, numbers_in, parse_duration,
)
from ..strategy.spec import PriceSmaCondition, SmaCrossCondition, fmt_num
from ..symbols import SymbolRegistry
from .spec import MAX_COOLDOWN_MIN, MAX_WINDOW_HOURS, AlertDraft, DraftCondition

_F = re.I

AskField = str  # "symbol" | "condition" | "interval" | "threshold" | "window"
FIELDS: tuple[str, ...] = ("symbol", "condition", "interval", "threshold", "window")

_NOT_ASSETS = {"RSI", "SMA", "EMA", "MACD", "ALERT", "NEWS", "PING", "TELL", "WHEN", "ABOVE", "BELOW", "PRICE",
               "DROP", "DROPS", "MOVE", "MOVES", "SMA50", "SMA200", "MA", "CROSS", "DAILY", "HOURLY", "UP", "DOWN"}

# ------------------------------------------------------------------ patterns

_IV_WORD = (r"(?:15\s*-?\s*min(?:ute)?s?|15m|(?:1|one)\s*-?\s*h(?:ou)?r|1h|hourly|(?:4|four)\s*-?\s*h(?:ou)?r|4h|"
            r"(?:1|one)\s*-?\s*day|1d|daily)")
_IV_OF_WORD = [("15m", re.compile(r"15", _F)), ("4h", re.compile(r"4|four", _F)), ("1d", re.compile(r"d", _F)),
               ("1h", re.compile(r"h", _F))]
_RSI = re.compile(rf"(?:(?P<iv>{_IV_WORD})(?:'s)?\s+)?\brsi\b(?:\s*\(\s*(?P<paren>\d{{1,3}})\s*\)|\s*-\s*(?P<dash>"
                  rf"\d{{1,3}})\b|\s+(?P<sp>\d{{1,3}})\b(?=\s+(?:is|drops?|falls?|goes|crosses|rises?|dips?|moves?|"
                  rf"gets|reads|closes|stays|sinks?|below|under|over|above|<|>|on)))?", _F)
_RSI_VAGUE = re.compile(r"\b(?:gets?|goes|is|turns?|becomes?|runs?)\s+(?:very\s+|really\s+|too\s+)?(?P<w>low|high|hot|"
                        r"cold|weak|strong|extreme)\b", _F)
_OVER_SOLD = re.compile(r"\b(oversold|overbought)\b", _F)
_CROSS_PLAIN = re.compile(r"\b(?:cross(?:es|ing)?|touch(?:es)?|tags?|hits?|reach(?:es)?)\s+(?:the\s+|its\s+)?$", _F)

_MOVE_VERB = (r"(?P<verb>moves?|moving|swings?|drops?|dropped|falls?|fell|dips?|dumps?|tanks?|sinks?|crash(?:es)?|"
              r"slides?|declines?|loses?|plunges?|is\s+down|goes\s+down|down|rises?|rose|gains?|is\s+up|goes\s+up|"
              r"up|jumps?|pumps?|climbs?|rall(?:y|ies)|surges?|spikes?|soars?|changes?)")
_PCT_MOVE = re.compile(rf"\b{_MOVE_VERB}\s+(?:by\s+)?(?:more\s+than\s+|at\s+least\s+|over\s+|>\s*|about\s+|around\s+|"
                       rf"roughly\s+)?(?P<pct>{NUM})\s*%", _F)
_PCT_NOUN = re.compile(rf"(?P<pct>{NUM})\s*%\s+(?:(?P<noun>drop|fall|decline|dip|dump|crash|sell-?off|rise|gain|pump|"
                       rf"jump|rally|move|swing|change|spike|surge|plunge)|(?P<dir>down|up|lower|higher))\b", _F)
_EITHER = re.compile(r"\b(?:either|any|both)\s+(?:direction|way)s?\b|\bup\s+or\s+down\b|\bdown\s+or\s+up\b", _F)
_DOWN = re.compile(r"drop|fell|fall|dip|dump|tank|sink|crash|slid|declin|los|plunge|down|lower|sell", _F)
_UP = re.compile(r"ris|rose|gain|up|jump|pump|climb|rall|surg|spik|soar|higher", _F)
_VAGUE_MOVE = re.compile(r"\b(?P<w>pumps?|pumping|dumps?|dumping|moons?|mooning|tanks?|tanking|crash(?:es)?|"
                         r"crashing|rall(?:y|ies)|surges?|spikes?|plunges?|soars?|goes\s+up|goes\s+down|drops?|"
                         r"falls?|rises?|jumps?|moves?\s+a\s+lot|big\s+move|breaks?\s+out)\b"
                         r"(?!\s+(?:below|under|above|over|to|through|past|beneath|the\b))", _F)

_LEVEL = re.compile(rf"(?P<op>above|over|below|under|beneath|past|through|hits?|reach(?:es)?|touch(?:es)?|tops?|"
                    rf"to|>|<)\s*(?:the\s+)?(?:level\s+of\s+|price\s+of\s+|mark\s+of\s+)?(?P<cur>\$|usd\s*)?\s*"
                    rf"(?P<v>\d+(?:\.\d+)?)(?![\d.])\s*(?P<k>k\b)?(?P<usd>\s*(?:usd[tc]?|dollars|bucks)\b)?"
                    rf"(?!\s*(?:%|{UNIT}\b|x\b))", _F)
_LEVEL_OPS = {"above": "above", "over": "above", "past": "above", "tops": "above", "top": "above", ">": "above",
              "below": "below", "under": "below", "beneath": "below", "<": "below"}

_NEWS = re.compile(r"\b(?P<t1>negative|bad|bearish|grim|gloomy|scary|fud|positive|good|bullish|upbeat|great|"
                   r"optimistic)\s+(?:crypto\s+)?(?:news|headlines?|sentiment|press|coverage|stories)\b|"
                   r"\b(?:news|headlines?|sentiment|press|coverage)\s+(?:is\s+|are\s+|turns?\s+|turned\s+|goes\s+|"
                   r"gets?\s+|becomes?\s+|is\s+getting\s+|looks?\s+)?(?:very\s+|really\s+)?(?P<t2>negative|bad|bearish|"
                   r"positive|good|bullish|upbeat)\b", _F)
_NEWS_LEVEL = re.compile(rf"sentiment\s+(?:score\s+)?(?:is\s+)?(?P<op>below|under|<|above|over|>)\s*(?P<v>-?{NUM})", _F)
_NEGATIVE_TONE = {"negative", "bad", "bearish", "grim", "gloomy", "scary", "fud"}

_COOLDOWN_RES = [
    re.compile(rf"(?:at\s+most|no\s+more\s+than|not\s+more\s+than|max(?:imum)?|only|just|up\s+to)\s+once\s+(?:(?:every|"
               rf"per|a|an|each|in)\s+)?(?P<n>{NUM}|a|an|one)?\s*-?\s*(?P<unit>{UNIT})\b", _F),
    re.compile(rf"cool\s*-?\s*down\s*(?:of|:|=|is|period\s+of)?\s*(?P<n>{NUM}|a|an|one)?\s*-?\s*(?P<unit>{UNIT})\b", _F),
    re.compile(rf"(?:don'?t|do\s+not|never)\s+(?:alert|ping|notify|remind|message|spam|bug)\s+me\s+more\s+(?:than\s+|"
               rf"often\s+than\s+)?once\s+(?:every|per|a|an|each|in)\s+(?P<n>{NUM}|a|an|one)?\s*-?\s*(?P<unit>{UNIT})\b",
               _F),
]

_UNSUPPORTED: dict[str, tuple[str, re.Pattern[str]]] = {
    "volume": ("Volume conditions aren't supported yet.",
               re.compile(r"\bvolume\b(?!\s+of\s+news)", _F)),
    "macd": ("MACD isn't supported yet.", re.compile(r"\bmacd\b", _F)),
    "ema": ("EMA isn't supported yet: use a simple moving average (SMA).", re.compile(r"\bemas?\b|exponential", _F)),
    "bollinger": ("Bollinger Bands aren't supported yet.", re.compile(r"\bbollinger|\bbbands?\b", _F)),
    "other_indicator": ("Other indicators (VWAP, ATR, stochastic, Ichimoku) aren't supported yet.",
                        re.compile(r"\b(?:vwap|atr|stoch(?:astic)?|ichimoku|supertrend|fibonacci|fib)\b", _F)),
    "derivatives": ("Funding, open interest and liquidation alerts aren't supported yet.",
                    re.compile(r"\b(?:funding(?:\s+rate)?|open\s+interest|liquidations?)\b", _F)),
    "on_chain": ("On-chain and whale-wallet alerts aren't supported: TickerSage reads exchange market data only.",
                 re.compile(r"\b(?:whales?|on-?chain|wallet\s+(?:moves|transfers?)|inflows?|outflows?)\b", _F)),
}

QUESTIONS: dict[str, tuple[str, str]] = {
    "symbol": ("Which asset should this alert watch? One asset per alert.", "ETH"),
    "condition": ("What should trigger the alert? Supported: RSI levels, price vs an SMA, SMA crosses, a % move over a "
                  "window, a price level, and headline sentiment.", "RSI(14) on 1h drops below 30"),
    "interval": ("Which candles should it use: 15m, 1h, 4h or 1d?", "1h"),
    "threshold": ("What level should trigger it?", "30"),
    "window": ("Over what period should the move be measured?", "24 hours"),
}


@dataclass
class Parsed:
    draft: AlertDraft
    issues: dict[str, str] = field(default_factory=dict)  # field -> why it can't be read yet
    notes: list[str] = field(default_factory=list)
    symbols_seen: list[str] = field(default_factory=list)
    intervals_seen: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)  # messages
    blocked: bool = False  # an unsupported feature: the conditions were cleared and a question asked


def _blank(text: str, start: int, end: int) -> str:
    return text[:start] + " " * (end - start) + text[end:]


def _iv_from_word(word: str) -> str:
    w = word.lower()
    if w in ("hourly",):
        return "1h"
    if w in ("daily",):
        return "1d"
    for iv, p in _IV_OF_WORD:
        if p.search(w):
            return iv
    return "1h"


def _clause_end(text: str, start: int) -> int:
    m = re.compile(r"[;.!?]|,(?!\d)|\b(?:and|or|while|with|but|then)\b", _F).search(text, start + 3)
    return m.start() if m else len(text)


def _clause_interval(text: str, start: int, end: int) -> str | None:
    found = detect_intervals(text[start:end])
    if len(found) == 1:
        return found[0]
    m = re.search(rf"\b{_IV_WORD}(?=\s+(?:candles?|charts?|bars?|timeframe|tf)\b)", text[start:end], _F)
    return _iv_from_word(m.group(0)) if m else None


def _window_after(text: str, start: int, end: int) -> float | None:
    """The window, in hours, written after a move ("drops 5% in 24h") or just before it ("a 24h 5% drop")."""
    lb = _LOOKBACK.search(text[end: end + 48])
    hit = lb if lb and not text[end: end + lb.start()].strip(" ,") else None
    hit = hit or _PRE_LOOKBACK.search(text[max(0, start - 24): start]) or lb
    if not hit:
        return None
    count, key = _duration(hit.group("n"), hit.group("unit"))
    return _to_hours(count, key, None)


# ------------------------------------------------------------------ main entry point


def parse(text: str, registry: SymbolRegistry) -> Parsed:
    t = normalize(text)
    parsed = Parsed(draft=AlertDraft())
    d = parsed.draft
    parsed.intervals_seen = detect_intervals(t)
    found: list[tuple[int, DraftCondition]] = []

    # Asset: one per alert.
    symbols = [s for s in registry.extract(t) if s.upper() not in _NOT_ASSETS]
    parsed.symbols_seen = symbols
    if len(symbols) == 1:
        d.symbol = symbols[0].upper()
    elif len(symbols) > 1:
        parsed.issues["symbol"] = (f"You mentioned {', '.join(symbols[:4])}. Each alert watches one asset: which one "
                                   "should this one watch?")

    work = t
    # Cooldown first, so "once a day" isn't read as a window.
    for p in _COOLDOWN_RES:
        if m := p.search(work):
            count, key = _duration(m.group("n"), m.group("unit"))
            hours = _to_hours(count, key, None)
            if hours is not None:
                d.cooldown_minutes = max(1, min(MAX_COOLDOWN_MIN, int(round(hours * 60))))
                parsed.notes.append(f"'{m.group(0).strip()}' read as a cooldown of {d.cooldown_minutes} minutes")
            work = _blank(work, m.start(), m.end())
            break

    # SMA crosses and price vs SMA (the Strategy Studio reader), then "crosses the 50 SMA" with no direction.
    sma, _ = _sma_conditions(work, parsed.notes)
    for pos, c in sma:
        end = _clause_end(work, pos)
        iv = _clause_interval(t, pos, end)
        if isinstance(c, SmaCrossCondition):
            op = "above" if c.op.endswith("above") else "below"
            found.append((pos, DraftCondition(kind="sma_cross", fast=c.fast, slow=c.slow, op=op, interval=iv)))
        elif isinstance(c, PriceSmaCondition):
            op = "above" if c.op.endswith("above") else "below"
            found.append((pos, DraftCondition(kind="price_vs_sma", period=c.period, op=op, interval=iv)))
    for m in _MA_REF.finditer(work):
        if any(abs(pos - m.start()) < 60 for pos, _ in sma):
            continue
        if _CROSS_PLAIN.search(work[max(0, m.start() - 30): m.start()]):
            period = int(m.group("a") or m.group("b") or m.group("c"))
            iv = _clause_interval(t, m.start(), _clause_end(work, m.start()))
            found.append((m.start(), DraftCondition(kind="price_vs_sma", period=period, op="crosses", interval=iv)))
            parsed.notes.append(f"'crosses the SMA({period})' read as the close crossing it from whichever side it "
                                "is on at the first check")
    for m in _MA_REF.finditer(work):
        work = _blank(work, m.start(), m.end())
    for m in re.finditer(r"\b(golden|death)\s+cross\b", work, _F):
        work = _blank(work, m.start(), m.end())

    # RSI. A period-looking number before "RSI" with a time unit ("4h RSI") is the interval, not the period.
    rsi_seen = False
    for m in _RSI.finditer(work):
        rsi_seen = True
        end = _clause_end(work, m.end() - 1)
        window = work[m.end(): min(end, m.end() + 60)]
        period_s = m.group("paren") or m.group("dash") or m.group("sp")
        period = int(period_s) if period_s else 14
        iv = _iv_from_word(m.group("iv")) if m.group("iv") else _clause_interval(t, m.start(), end)
        best = None
        for op, pattern in _RSI_OPS:
            hit = pattern.search(window)
            if hit and (best is None or hit.start() < best[0]):
                best = (hit.start(), op, hit)
        cond = DraftCondition(kind="rsi", period=period, interval=iv)
        span_end = m.end()
        if best is not None:
            _, op, hit = best
            span_end = m.end() + hit.end()
            if op in ("oversold", "overbought"):
                cond.op, cond.value = ("below", 30.0) if op == "oversold" else ("above", 70.0)
                parsed.notes.append(f"'{op}' read as RSI {cond.op} {fmt_num(cond.value)}")
            else:
                value = float(hit.group("v"))
                if op == "reaches":
                    op = "above" if value > 50 else "below"
                cond.op = "below" if "below" in op else "above"
                cond.value = value
        elif vague := _RSI_VAGUE.search(window):
            cond.op = "below" if vague.group("w").lower() in ("low", "cold", "weak") else "above"
            span_end = m.end() + vague.end()
        if not period_s:
            parsed.notes.append("RSI period not given: using RSI(14)")
        found.append((m.start(), cond))
        work = _blank(work, m.start(), min(span_end, len(work)))
    if not rsi_seen and (m := _OVER_SOLD.search(work)):
        sold = m.group(1).lower() == "oversold"
        iv = _clause_interval(t, max(0, m.start() - 40), _clause_end(work, m.start()))
        found.append((m.start(), DraftCondition(kind="rsi", period=14, interval=iv, op="below" if sold else "above",
                                                value=30.0 if sold else 70.0)))
        parsed.notes.append(f"'{m.group(1)}' read as RSI(14) {'below 30' if sold else 'above 70'}")
        work = _blank(work, m.start(), m.end())

    # % moves over a window.
    for p in (_PCT_MOVE, _PCT_NOUN):
        for m in p.finditer(work):
            word = (m.groupdict().get("verb") or m.groupdict().get("noun") or m.groupdict().get("dir") or "").lower()
            near = work[max(0, m.start() - 20): m.end() + 40]
            if _EITHER.search(near) or re.match(r"(?:moves?|moving|swings?|changes?|move|swing|change)$", word):
                direction = "either"
            elif _DOWN.search(word):
                direction = "down"
            elif _UP.search(word):
                direction = "up"
            else:
                direction = "either"
            hours = _window_after(work, m.start(), m.end())
            found.append((m.start(), DraftCondition(kind="price_change", direction=direction,
                                                    pct=float(m.group("pct")), window_hours=hours)))
            work = _blank(work, m.start(), m.end())

    # Price levels.
    for m in _LEVEL.finditer(work):
        priced = bool(m.group("cur") or m.group("k") or m.group("usd"))
        value = float(m.group("v")) * (1000 if m.group("k") else 1)
        if not priced and not re.search(r"\b(?:price|trades?|trading|is|at|goes|gets)\b", work[max(0, m.start() - 25):
                                                                                             m.start()], _F):
            if not (d.symbol and re.search(rf"{re.escape(d.symbol)}\S*\s*$", work[max(0, m.start() - 12): m.start()],
                                           _F)):
                continue
        op_word = m.group("op").lower()
        op = _LEVEL_OPS.get(op_word, "reaches")
        found.append((m.start(), DraftCondition(kind="price_level", op=op, price=value)))
        if op == "reaches":
            parsed.notes.append(f"'{m.group(0).strip()}' read as the price reaching ${value:,g} from whichever side "
                                "it is on at the first check")
        work = _blank(work, m.start(), m.end())

    # Headline sentiment.
    if m := _NEWS.search(work):
        tone_word = (m.group("t1") or m.group("t2")).lower()
        tone = "negative" if tone_word in _NEGATIVE_TONE else "positive"
        cond = DraftCondition(kind="news_sentiment", tone=tone)
        if lvl := _NEWS_LEVEL.search(t):
            cond.threshold = float(lvl.group("v"))
        found.append((m.start(), cond))
        work = _blank(work, m.start(), m.end())
    elif lvl := _NEWS_LEVEL.search(work):
        tone = "negative" if lvl.group("op").lower() in ("below", "under", "<") else "positive"
        found.append((lvl.start(), DraftCondition(kind="news_sentiment", tone=tone, threshold=float(lvl.group("v")))))
        work = _blank(work, lvl.start(), lvl.end())

    # A move with no size ("when BTC pumps"): asked about, never guessed.
    if not any(c.kind == "price_change" for _, c in found):
        for m in _VAGUE_MOVE.finditer(work):
            if re.match(r"\s+(?:below|under|above|over|to|through|past|beneath|the)\b", t[m.end():], _F):
                continue  # "falls under 75k": a level, read above
            w = m.group("w").lower()
            if re.match(r"moves?|big|breaks?", w):
                direction = "either"
            elif "moon" not in w and _DOWN.search(w):
                direction = "down"
            else:
                direction = "up"
            hours = _window_after(work, m.start(), m.end())
            found.append((m.start(), DraftCondition(kind="price_change", direction=direction, window_hours=hours)))
            break

    found.sort(key=lambda x: x[0])
    # One timeframe named anywhere ("ETH on the daily chart is below the 20 SMA") applies to every indicator.
    if len(parsed.intervals_seen) == 1:
        for _, c in found:
            if c.kind in ("rsi", "price_vs_sma", "sma_cross") and c.interval is None:
                c.interval = parsed.intervals_seen[0]  # type: ignore[assignment]
    d.conditions = [c for _, c in found][:3]
    if len(found) > 3:
        parsed.issues["condition"] = "An alert can combine at most three conditions. Which ones should this one use?"
    if len(found) > 1 and re.search(r"\bor\b|\beither\b(?!\s+(?:direction|way))",
                                    t[found[0][0]: found[-1][0]], _F):
        d.logic = "any"

    # An unsupported feature blocks the alert, so it is never quietly turned into a different one.
    for _key, (msg, pattern) in _UNSUPPORTED.items():
        if pattern.search(t):
            parsed.unsupported.append(msg)
    if parsed.unsupported:
        block(parsed)

    # A window that is too long, or counted in candles, is asked about.
    for c in d.conditions:
        if c.kind == "price_change" and c.window_hours is not None and c.window_hours > MAX_WINDOW_HOURS:
            parsed.issues["window"] = (f"{fmt_num(c.window_hours / 24)} days is longer than the 30 days an alert can "
                                       "measure. Over what period should the move be measured?")
            c.window_hours = None
    parsed.notes = list(dict.fromkeys(parsed.notes))
    return parsed


def block(parsed: Parsed) -> None:
    parsed.blocked = True
    parsed.draft.conditions = []
    parsed.issues["condition"] = " ".join(parsed.unsupported) + " " + QUESTIONS["condition"][0]


# ------------------------------------------------------------------ answers


def _first_number(text: str) -> tuple[float, bool, bool] | None:
    """(value, written with %, written as a price ($ or k)) for the first number in an answer."""
    t = normalize(text)
    m = re.search(rf"(?P<cur>\$)?\s*(?P<v>-?{NUM})\s*(?P<k>k\b)?\s*(?P<pct>%)?", t, _F)
    if not m:
        return None
    value = float(m.group("v")) * (1000 if m.group("k") else 1)
    return value, bool(m.group("pct")), bool(m.group("cur") or m.group("k"))


def apply_answers(parsed: Parsed, answers: dict[str, str], registry: SymbolRegistry) -> Parsed:
    """Fill what the user answered, field by field. Each answer is read on its own."""
    d = parsed.draft
    for field_name in FIELDS:
        raw = (answers.get(field_name) or "").strip()
        if not raw:
            continue
        parsed.issues.pop(field_name, None)
        if field_name == "symbol":
            found = [s for s in registry.extract(normalize(raw)) if s.upper() not in _NOT_ASSETS]
            if len(found) == 1:
                d.symbol = found[0].upper()
            else:
                try:
                    d.symbol = registry.base(registry.resolve(raw))
                except Exception:
                    parsed.issues["symbol"] = f"I couldn't find an asset in '{raw}'."
        elif field_name == "condition":
            sub = parse(raw, registry)
            if sub.unsupported:
                parsed.issues["condition"] = " ".join(sub.unsupported) + " " + QUESTIONS["condition"][0]
            elif not sub.draft.conditions:
                parsed.issues["condition"] = f"I couldn't read a supported condition in '{raw}'."
            else:
                d.conditions, d.logic = sub.draft.conditions, sub.draft.logic
                parsed.blocked = False
                parsed.notes += [n for n in sub.notes if n not in parsed.notes]
        elif field_name == "interval":
            ivs = detect_intervals(f"{raw} candles") or detect_intervals(raw)
            if len(ivs) != 1:
                parsed.issues["interval"] = f"I couldn't read '{raw}' as 15m, 1h, 4h or 1d."
                continue
            for c in d.conditions:
                if c.kind in ("rsi", "price_vs_sma", "sma_cross") and c.interval is None:
                    c.interval = ivs[0]  # type: ignore[assignment]
        elif field_name == "threshold":
            got = _first_number(raw)
            target = next((c for c in d.conditions if "threshold" in c.missing()), None)
            if got is None or target is None:
                parsed.issues["threshold"] = f"I couldn't read a level in '{raw}'."
                continue
            value, _pct, _priced = got
            if target.kind == "rsi":
                target.value = value
                if target.op is None:
                    low = re.search(r"\b(below|under|less|drops?|falls?)\b", raw, _F)
                    high = re.search(r"\b(above|over|more|rises?|climbs?)\b", raw, _F)
                    target.op = "below" if low else "above" if high else ("below" if value < 50 else "above")
            elif target.kind == "price_change":
                target.pct = abs(value)
            else:
                target.price = value
        elif field_name == "window":
            span = parse_duration(raw)
            hours = _to_hours(span[0], span[1], None) if span else None
            if hours is None:
                parsed.issues["window"] = f"I couldn't read '{raw}' as a period."
                continue
            for c in d.conditions:
                if c.kind == "price_change" and c.window_hours is None:
                    c.window_hours = hours
    return parsed


def questions_for(parsed: Parsed) -> list[tuple[str, str, str]]:
    """(field, question, example) for everything still missing, in a fixed order."""
    d = parsed.draft
    missing: dict[str, str | None] = {}
    if not d.symbol:
        missing["symbol"] = parsed.issues.get("symbol")
    if not d.conditions:
        missing["condition"] = parsed.issues.get("condition")
    for c in d.conditions:
        for f in c.missing():
            missing.setdefault(f, None)
    for f, reason in parsed.issues.items():
        missing.setdefault(f, reason)
        missing[f] = missing[f] or reason
    out = []
    for f in FIELDS:
        if f not in missing:
            continue
        question, example = QUESTIONS[f]
        reason = missing[f]
        if f == "threshold" and not reason:
            c = next((c for c in d.conditions if "threshold" in c.missing()), None)
            if c is not None and c.kind == "rsi":
                question, example = "At what RSI level, and above or below it?", "below 30"
            elif c is not None and c.kind == "price_change":
                question, example = "How big a move should trigger it, in %?", "5%"
            elif c is not None:
                question, example = "At what price?", "$70,000"
        if f == "window" and not reason:
            c = next((c for c in d.conditions if c.kind == "price_change" and c.window_hours is None), None)
            if c is not None and c.pct is not None:
                question = f"Over what period should the {fmt_num(c.pct)}% move be measured?"
        if f == "interval" and not reason:
            kinds = {c.kind for c in d.conditions if c.interval is None and c.kind in ("rsi", "price_vs_sma",
                                                                                        "sma_cross")}
            what = "the RSI" if kinds == {"rsi"} else "the SMA" if kinds <= {"price_vs_sma", "sma_cross"} else \
                "the indicators"
            question = f"Which candles should {what} use: 15m, 1h, 4h or 1d?"
        if reason:
            question = reason if reason.rstrip().endswith("?") else f"{reason} {question}"
        out.append((f, question, example))
    return out


def written_numbers(text: str) -> list[float]:
    return numbers_in(text)
