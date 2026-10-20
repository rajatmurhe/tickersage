"""The alert spec: a validated, deterministic description of when to notify, on one asset.

A rule is one to three conditions joined by all/any, plus a cooldown. Every condition is read-only market
data the existing tools already serve (candles, the 24h ticker, headlines), so checking a rule never needs a
new data source and never touches an account.

Alerts are edge-triggered: a rule fires when its conditions become true, then waits until they are false
again (re-arms) before it can fire again, and never fires twice within its cooldown.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..strategy.spec import INTERVAL_HOURS, RSI_DEFAULT_PERIOD, fmt_hours, fmt_num

Interval = Literal["15m", "1h", "4h", "1d"]
INTERVALS: tuple[str, ...] = ("15m", "1h", "4h", "1d")
DEFAULT_COOLDOWN_MIN = 60
MAX_COOLDOWN_MIN = 7 * 24 * 60
MAX_WINDOW_HOURS = 30 * 24
CANDLES = 500  # get_klines' maximum, so one call covers the longest SMA and the replay fixtures match
# VADER's own convention: a compound score at or below -0.05 is negative, at or above +0.05 positive.
NEWS_NEGATIVE = -0.05
NEWS_POSITIVE = 0.05

DISCLAIMER = ("Alerts report what market data did. They are not signals to trade and not investment advice; "
              "TickerSage has no order, wallet or account tools.")


class RsiAlert(BaseModel):
    kind: Literal["rsi"] = "rsi"
    period: int = Field(default=RSI_DEFAULT_PERIOD, ge=2, le=200)
    interval: Interval
    op: Literal["below", "above"]
    value: float = Field(gt=0, lt=100)


class PriceSmaAlert(BaseModel):
    """The last close against a simple moving average of the closes. `crosses` fires when the close gets to the
    other side of the SMA from the one it was on at the first check."""

    kind: Literal["price_vs_sma"] = "price_vs_sma"
    period: int = Field(ge=2, le=CANDLES - 1)
    interval: Interval
    op: Literal["below", "above", "crosses"]


class SmaCrossAlert(BaseModel):
    """The fast SMA above (golden cross) or below (death cross) the slow one."""

    kind: Literal["sma_cross"] = "sma_cross"
    fast: int = Field(ge=2, le=CANDLES - 1)
    slow: int = Field(ge=2, le=CANDLES - 1)
    interval: Interval
    op: Literal["above", "below"]


class PriceChangeAlert(BaseModel):
    """Close-to-close change over a window: 'drops 5% in 24 hours'. `either` is a move of that size either way."""

    kind: Literal["price_change"] = "price_change"
    direction: Literal["up", "down", "either"]
    pct: float = Field(gt=0, le=1000)
    window_hours: float = Field(gt=0, le=MAX_WINDOW_HOURS)


class PriceLevelAlert(BaseModel):
    """The last traded price against a level. `reaches` fires when the price gets to the level from whichever
    side it was on at the first check."""

    kind: Literal["price_level"] = "price_level"
    op: Literal["above", "below", "reaches"]
    price: float = Field(gt=0)


class NewsAlert(BaseModel):
    """The average VADER score of the latest headlines about the asset."""

    kind: Literal["news_sentiment"] = "news_sentiment"
    tone: Literal["negative", "positive"]
    threshold: float | None = Field(default=None, ge=-1, le=1, description="Defaults to -0.05 / +0.05")


AlertCondition = Annotated[
    RsiAlert | PriceSmaAlert | SmaCrossAlert | PriceChangeAlert | PriceLevelAlert | NewsAlert,
    Field(discriminator="kind"),
]
ConditionKind = Literal["rsi", "price_vs_sma", "sma_cross", "price_change", "price_level", "news_sentiment"]


class DraftCondition(BaseModel):
    """A condition as far as it could be read: any field may still be missing."""

    kind: ConditionKind
    op: str | None = None
    value: float | None = None
    period: int | None = None
    interval: Interval | None = None
    fast: int | None = None
    slow: int | None = None
    direction: Literal["up", "down", "either"] | None = None
    pct: float | None = None
    window_hours: float | None = None
    price: float | None = None
    tone: Literal["negative", "positive"] | None = None
    threshold: float | None = None

    def missing(self) -> list[str]:
        """The clarifying-question fields this condition still needs."""
        out = []
        if self.kind in ("rsi", "price_vs_sma", "sma_cross") and self.interval is None:
            out.append("interval")
        if (self.kind == "rsi" and self.value is None) or (self.kind == "price_change" and self.pct is None) or \
                (self.kind == "price_level" and self.price is None):
            out.append("threshold")
        if self.kind == "price_change" and self.window_hours is None:
            out.append("window")
        return out


class AlertDraft(BaseModel):
    symbol: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,20}$")
    conditions: list[DraftCondition] = Field(default_factory=list, max_length=3)
    logic: Literal["all", "any"] = "all"
    cooldown_minutes: int = Field(default=DEFAULT_COOLDOWN_MIN, ge=1, le=MAX_COOLDOWN_MIN)


class AlertSpec(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z0-9]{1,20}$")
    conditions: list[AlertCondition] = Field(min_length=1, max_length=3)
    logic: Literal["all", "any"] = "all"
    cooldown_minutes: int = Field(default=DEFAULT_COOLDOWN_MIN, ge=1, le=MAX_COOLDOWN_MIN)


def window_interval(hours: float) -> tuple[str, int] | None:
    """The coarsest candle interval that measures a window exactly within one get_klines call, and the number
    of candles the window spans: 24h -> ('1h', 24), 7 days -> ('1h', 168), 30 days -> ('1d', 30)."""
    for iv in ("1h", "15m", "4h", "1d"):
        bars = hours / INTERVAL_HOURS[iv]
        if abs(bars - round(bars)) < 1e-9 and 1 <= round(bars) < CANDLES:
            return iv, int(round(bars))
    return None


# ------------------------------------------------------------------ wording


def describe(c: AlertCondition | DraftCondition) -> str:
    """One condition in words. Missing fields (on a draft) read as '?'."""

    def n(x: float | None) -> str:
        return "?" if x is None else fmt_num(x)

    on = f" on {c.interval} candles" if getattr(c, "interval", None) else ""
    if c.kind == "rsi":
        return f"RSI({c.period or RSI_DEFAULT_PERIOD}){on} is {c.op or '?'} {n(c.value)}"
    if c.kind == "price_vs_sma":
        verb = "crosses" if c.op == "crosses" else f"is {c.op or '?'}"
        return f"Close {verb} SMA({n(c.period)}){on}"
    if c.kind == "sma_cross":
        return f"SMA({n(c.fast)}) is {c.op or '?'} SMA({n(c.slow)}){on}"
    if c.kind == "price_change":
        verb = {"up": "rises", "down": "falls", "either": "moves"}.get(c.direction or "", "moves")
        within = fmt_hours(c.window_hours) if c.window_hours else "?"
        either = " either way" if c.direction == "either" else ""
        return f"Price {verb} {n(c.pct)}% or more{either} within {within}"
    if c.kind == "price_level":
        price = "?" if c.price is None else f"${c.price:,.{_decimals(c.price)}f}"
        verb = {"above": "is above", "below": "is below", "reaches": "reaches"}.get(c.op or "", "?")
        return f"Price {verb} {price}"
    level = c.threshold if c.threshold is not None else (NEWS_NEGATIVE if c.tone == "negative" else NEWS_POSITIVE)
    return f"Headline sentiment is {c.tone or '?'} (average score {'at or below' if c.tone == 'negative' else 'at or above'} {level:+g})"


def _decimals(price: float) -> int:
    return 0 if price >= 1000 and price == int(price) else 2 if price >= 1 else 6


def summarize(spec: AlertSpec | AlertDraft) -> list[str]:
    lines = [f"{spec.symbol.upper() if spec.symbol else '? (asset not chosen yet)'}"]
    joiner = " AND " if spec.logic == "all" else " OR "
    if spec.conditions:
        lines.append("Notify when " + joiner.join(describe(c) for c in spec.conditions))
    lines.append(f"Fires when that becomes true, then re-arms once it is false again; at most once every "
                 f"{fmt_hours(spec.cooldown_minutes / 60)}")
    return lines
