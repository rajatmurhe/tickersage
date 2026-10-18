"""Number rounding, formatting and extraction.

Tools return display-ready rounded numbers, templates format them, and the verifier
extracts numbers from free text with the same rules. Keeping all three here means a
number written by the template can always be traced back to the tool output.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# ---------------------------------------------------------------- rounding


def price_decimals(x: float) -> int:
    """2 decimals at or above $1, otherwise 4 significant digits."""
    ax = abs(x)
    if ax == 0 or ax >= 1:
        return 2
    return min(10, 4 - int(math.floor(math.log10(ax))) - 1)


def round_price(x: float) -> float:
    return round(float(x), price_decimals(x))


def r2(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), 2)


# -------------------------------------------------------------- formatting


def fmt_price(x: float) -> str:
    return f"${x:,.{price_decimals(x)}f}"


def fmt_pct(x: float, signed: bool = True) -> str:
    return f"{x:+.2f}%" if signed else f"{x:.2f}%"


def fmt_big(x: float, prefix: str = "$") -> str:
    ax = abs(x)
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if ax >= div:
            return f"{prefix}{x / div:,.2f}{suffix}"
    return f"{prefix}{x:,.2f}"


def fmt_num(x: float) -> str:
    return f"{x:,.2f}"


# -------------------------------------------------------------- extraction

_MULTIPLIERS = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "million": 1e6, "mn": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "trillion": 1e12,
}

_NUM_RE = re.compile(
    r"(?<![\w.$])(?P<cur>\$)?\s?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"(?:\s?(?P<suf>%|bps|x\b|×|[kKmMbBtT]\b|bn\b|mn\b|thousand\b|million\b|billion\b|trillion\b))?"
    r"(?![\w])"
)

_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"

# Spans that contain digits but are not market figures.
_EXEMPT_PATTERNS = [
    re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]"),  # citation markers [1], [1, 2]
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:UTC|GMT|Z)?"),
    re.compile(rf"\b{_MONTHS}\s+\d{{4}}\b", re.I),  # "October 2026"
    re.compile(rf"\b{_MONTHS}\s+\d{{1,2}}(?!\d)(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?", re.I),
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTHS + r"(?:\s+\d{4})?", re.I),
    re.compile(
        r"\b\d+\s*-?\s*(?:h|hr|hrs|hour|hours|d|day|days|wk|wks|week|weeks|month|months|min|mins|minute|minutes"
        r"|sec|secs|seconds|yr|yrs|year|years)\b",
        re.I,
    ),
    re.compile(r"\b(?:RSI|SMA|EMA|MA|ATR)\s*[-(]?\s*\d+\)?", re.I),
    re.compile(r"\b\d+\s*-?\s*(?:period|periods|candle|candles|bar|bars|interval|intervals)\b", re.I),
    re.compile(r"(?m)^\s*\d+[.)]\s"),  # list markers
    re.compile(r"(?<![$\d,.])\b(?:19|20)\d{2}\b(?![\d,.%])"),  # bare years
    re.compile(r"\b(?:top|first|last)\s+\d+\b", re.I),
]


@dataclass(frozen=True)
class ExtractedNumber:
    raw: str
    value: float  # as written, before the K/M/B multiplier
    decimals: int
    multiplier: float
    is_pct: bool
    has_currency: bool

    @property
    def scaled(self) -> float:
        return self.value * self.multiplier

    @property
    def is_trivial(self) -> bool:
        """Bare small counting numbers ("3 of the 5 headlines") are not checked."""
        return (
            not self.is_pct
            and not self.has_currency
            and self.multiplier == 1
            and self.decimals == 0
            and self.value <= 10
        )


def strip_exempt(text: str) -> str:
    for pattern in _EXEMPT_PATTERNS:
        text = pattern.sub(" ", text)
    return text


def extract_numbers(text: str, apply_exemptions: bool = True) -> list[ExtractedNumber]:
    if apply_exemptions:
        text = strip_exempt(text)
    out: list[ExtractedNumber] = []
    for m in _NUM_RE.finditer(text):
        int_part = m.group("int").replace(",", "")
        dec = m.group("dec") or ""
        suffix = (m.group("suf") or "").strip()
        value = float(int_part + dec)
        mult = _MULTIPLIERS.get(suffix.lower(), 1.0) if suffix not in ("%", "bps", "x", "×") else 1.0
        out.append(
            ExtractedNumber(
                raw=m.group(0).strip(),
                value=value,
                decimals=max(0, len(dec) - 1),
                multiplier=mult,
                is_pct=suffix == "%",
                has_currency=bool(m.group("cur")),
            )
        )
    return out


def matches_value(num: ExtractedNumber, target: float) -> bool:
    """True when `target` rounds (or truncates) to the number as written."""
    target = abs(target)
    value = abs(num.value)
    step = 10.0 ** (-num.decimals)
    candidates = [target / num.multiplier]
    if num.is_pct:
        candidates.append(target * 100)  # fraction stored, percent written
    for tv in candidates:
        diff = tv - value
        eps = 1e-9 * max(1.0, tv)
        if -0.5 * step - eps <= diff < step + eps and (diff <= 0.5 * step + eps or _truncates(tv, value, num)):
            return True
    return False


def _truncates(tv: float, value: float, num: ExtractedNumber) -> bool:
    factor = 10 ** num.decimals
    return abs(math.trunc(tv * factor + 1e-9) / factor - value) < 1e-9 * max(1.0, value)
