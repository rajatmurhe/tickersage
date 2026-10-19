"""The strategy spec: a validated, deterministic description of a rule-based idea.

Long-only spot rules on one asset. Entry is one to four indicator conditions; exits are a
take profit, a stop loss, a maximum holding time and optional exit signals. Sizes are a
percentage of a notional demo balance: nothing here is connected to an account.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..tools.market import Interval

INTERVAL_HOURS: dict[str, float] = {"15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 24.0}
DEMO_BALANCE = 10_000.0
DEFAULT_FEE_PCT = 0.1  # Binance spot taker fee, per side
DEFAULT_SLIPPAGE_PCT = 0.05  # per fill, against you
RSI_DEFAULT_PERIOD = 14
CANDLES = 500  # the most get_klines returns in one call; the backtest window

DISCLAIMER = (
    "Backtest on historical candles only: how these rules would have behaved in the past, not how they will "
    "behave next. Nothing here places orders. Not investment advice."
)


class RsiCondition(BaseModel):
    kind: Literal["rsi"] = "rsi"
    period: int = Field(default=RSI_DEFAULT_PERIOD, ge=2, le=200)
    op: Literal["below", "above", "crosses_below", "crosses_above"]
    # Not range-checked here on purpose: the linter explains an RSI of 130 or 0.3 instead of a bare 422.
    value: float


class PriceSmaCondition(BaseModel):
    kind: Literal["price_vs_sma"] = "price_vs_sma"
    period: int = Field(ge=2, le=2000)
    op: Literal["above", "below", "crosses_above", "crosses_below"]


class SmaCrossCondition(BaseModel):
    """The fast SMA crossing the slow one (fast above slow is a golden cross)."""

    kind: Literal["sma_cross"] = "sma_cross"
    fast: int = Field(ge=2, le=2000)
    slow: int = Field(ge=2, le=2000)
    op: Literal["crosses_above", "crosses_below"]


class PriceChangeCondition(BaseModel):
    """Close-to-close change over a lookback: 'price drops 5% in 24 hours'."""

    kind: Literal["price_change"] = "price_change"
    direction: Literal["down", "up"]
    pct: float = Field(gt=0)
    lookback_hours: float = Field(gt=0)


Condition = Annotated[
    RsiCondition | PriceSmaCondition | SmaCrossCondition | PriceChangeCondition, Field(discriminator="kind")
]


class StrategyDraft(BaseModel):
    """What the compiler has so far. Every field may be missing; the linter checks whatever is present."""

    symbol: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,20}$")
    interval: Interval | None = None
    entry: list[Condition] = Field(default_factory=list, max_length=4)
    entry_logic: Literal["all", "any"] = "all"
    exit_signals: list[Condition] = Field(default_factory=list, max_length=4)
    take_profit_pct: float | None = Field(default=None, gt=0)
    stop_loss_pct: float | None = Field(default=None, gt=0)
    max_hold_hours: float | None = Field(default=None, gt=0)
    position_size_pct: float | None = Field(default=None, gt=0)
    demo_balance: float = Field(default=DEMO_BALANCE, gt=0, le=1e9)
    fee_pct: float = Field(default=DEFAULT_FEE_PCT, ge=0, le=5)
    slippage_pct: float = Field(default=DEFAULT_SLIPPAGE_PCT, ge=0, le=5)


class StrategySpec(StrategyDraft):
    """A complete spec: asset, candles, at least one entry condition and a position size."""

    symbol: str = Field(pattern=r"^[A-Za-z0-9]{1,20}$")
    interval: Interval
    entry: list[Condition] = Field(min_length=1, max_length=4)
    position_size_pct: float = Field(gt=0)


def bars_for_hours(hours: float, interval: str) -> int:
    """Whole candles covering `hours`, rounded up (a 30h hold on 4h candles is 8 candles)."""
    return max(1, math.ceil(hours / INTERVAL_HOURS[interval] - 1e-9))


def lookback_bars(cond: RsiCondition | PriceSmaCondition | SmaCrossCondition | PriceChangeCondition,
                  interval: str) -> int:
    """Candles of history a condition needs before it can be evaluated (crosses need one more)."""
    cross = 1 if getattr(cond, "op", "").startswith("crosses") else 0
    if isinstance(cond, RsiCondition):
        return cond.period + cross
    if isinstance(cond, PriceSmaCondition):
        return cond.period - 1 + cross
    if isinstance(cond, SmaCrossCondition):
        return max(cond.fast, cond.slow) - 1 + cross
    return bars_for_hours(cond.lookback_hours, interval)


# ------------------------------------------------------------------ wording


def fmt_num(x: float) -> str:
    """12 -> '12', 2.5 -> '2.5', 0.125 -> '0.125'."""
    return f"{x:.4f}".rstrip("0").rstrip(".") if x != int(x) else str(int(x))


def fmt_hours(hours: float) -> str:
    if hours >= 24 and hours % 24 == 0:
        days = int(hours // 24)
        return f"{days} day" + ("s" if days != 1 else "")
    if hours < 1:
        return f"{fmt_num(hours * 60)} min"
    return f"{fmt_num(hours)}h"


_OPS = {"below": "is below", "above": "is above", "crosses_below": "crosses below", "crosses_above": "crosses above"}


def describe(cond: RsiCondition | PriceSmaCondition | SmaCrossCondition | PriceChangeCondition) -> str:
    if isinstance(cond, RsiCondition):
        return f"RSI({cond.period}) {_OPS[cond.op]} {fmt_num(cond.value)}"
    if isinstance(cond, PriceSmaCondition):
        return f"Close {_OPS[cond.op]} SMA({cond.period})"
    if isinstance(cond, SmaCrossCondition):
        return f"SMA({cond.fast}) {_OPS[cond.op]} SMA({cond.slow})"
    verb = "falls" if cond.direction == "down" else "rises"
    return f"Close {verb} {fmt_num(cond.pct)}% or more within {fmt_hours(cond.lookback_hours)}"


def summarize(spec: StrategyDraft) -> list[str]:
    """Plain lines describing the spec, for the page and the diagram."""
    lines: list[str] = []
    if spec.symbol:
        candles = f"{spec.interval} candles" if spec.interval else "candles not chosen yet"
        lines.append(f"{spec.symbol.upper()} on {candles}, long only")
    joiner = " and " if spec.entry_logic == "all" else " or "
    if spec.entry:
        lines.append("Enter when " + joiner.join(describe(c) for c in spec.entry))
    if spec.take_profit_pct is not None:
        lines.append(f"Take profit at +{fmt_num(spec.take_profit_pct)}% from entry")
    if spec.stop_loss_pct is not None:
        lines.append(f"Stop loss at -{fmt_num(spec.stop_loss_pct)}% from entry")
    if spec.max_hold_hours is not None:
        lines.append(f"Exit after {fmt_hours(spec.max_hold_hours)} at most")
    for c in spec.exit_signals:
        lines.append(f"Exit when {describe(c)}")
    if spec.position_size_pct is not None:
        lines.append(f"Each trade uses {fmt_num(spec.position_size_pct)}% of a "
                     f"${spec.demo_balance:,.0f} demo balance")
    lines.append(f"Costs: {fmt_num(spec.fee_pct)}% fee and {fmt_num(spec.slippage_pct)}% slippage per fill")
    return lines
