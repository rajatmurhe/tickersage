"""Backtest a StrategySpec on OHLC candles. Every number is computed here, in Pandas and Python.

Timing (no look-ahead):
- Indicators at candle i use closes up to and including candle i.
- A signal at candle i's close fills at candle i+1's open: entries, exit signals and time exits.
- The take profit and stop loss are resting orders, checked against each candle's high and low from the
  entry candle on. A gap through a level fills at the open. When one candle touches both levels the stop
  is assumed to fill first, since a candle doesn't say which came first.
- One position at a time, long only. Each entry commits `position_size_pct` of current equity.
- Every fill pays `fee_pct` of its notional and fills `slippage_pct` worse than the trigger price.
- A trade still open on the last candle is closed at its close (exit reason `end_of_data`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

from ..numbers import r2, round_price
from .spec import (
    INTERVAL_HOURS, PriceChangeCondition, PriceSmaCondition, RsiCondition, SmaCrossCondition, StrategySpec,
    bars_for_hours, lookback_bars,
)

ExitReason = Literal["take_profit", "stop_loss", "exit_signal", "time_exit", "end_of_data"]


class Trade(BaseModel):
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: ExitReason
    bars_held: int
    notional: float
    pnl: float
    return_pct: float


class EquityPoint(BaseModel):
    time: datetime
    equity: float


class Metrics(BaseModel):
    initial_balance: float
    final_balance: float
    total_return_pct: float
    max_drawdown_pct: float
    trade_count: int
    win_rate_pct: float | None
    avg_trade_pct: float | None
    best_trade_pct: float | None
    worst_trade_pct: float | None
    exposure_pct: float
    fees_paid: float
    buy_and_hold_return_pct: float


class BacktestResult(BaseModel):
    symbol: str
    interval: str
    source: str | None
    as_of: datetime | None
    start: datetime
    end: datetime
    candles: int  # candles in the trading window (after the indicator warm-up)
    warmup_candles: int
    metrics: Metrics
    trades: list[Trade]
    equity_curve: list[EquityPoint]
    notes: list[str]


class BacktestError(ValueError):
    pass


# ------------------------------------------------------------------ candles


def candles_frame(data: dict, interval: str) -> pd.DataFrame:
    """get_klines output -> DataFrame indexed by open time, without a candle that hadn't closed yet."""
    df = pd.DataFrame(data.get("candles") or [])
    if df.empty:
        raise BacktestError("No candles came back for this asset and timeframe.")
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.set_index("open_time").sort_index()[["open", "high", "low", "close"]].astype(float)
    if data.get("as_of") is not None:
        as_of = pd.Timestamp(data["as_of"])
        as_of = as_of.tz_localize("UTC") if as_of.tzinfo is None else as_of.tz_convert("UTC")
        if as_of < df.index[-1] + pd.Timedelta(hours=INTERVAL_HOURS[interval]):
            df = df.iloc[:-1]  # the last candle was still open when the data was fetched
    return df


# ------------------------------------------------------------------ indicators


def rsi_series(close: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI at every candle (same formula as compute_metrics' RSI on the last candle)."""
    delta = close.diff().dropna()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rsi = pd.Series(np.where(loss == 0, 100.0, 100 - 100 / (1 + gain / loss.replace(0, np.nan))), index=delta.index)
    rsi[gain.isna()] = np.nan
    return rsi.reindex(close.index)


def _cross(diff: pd.Series, op: str) -> pd.Series:
    prev = diff.shift(1)
    if op == "crosses_above":
        return (prev <= 0) & (diff > 0)
    return (prev >= 0) & (diff < 0)


def condition_series(cond: object, df: pd.DataFrame, interval: str) -> pd.Series:
    """True on the candles whose close satisfies `cond`, using only data up to that close."""
    close = df["close"]
    if isinstance(cond, RsiCondition):
        diff = rsi_series(close, cond.period) - cond.value
    elif isinstance(cond, PriceSmaCondition):
        diff = close - close.rolling(cond.period).mean()
    elif isinstance(cond, SmaCrossCondition):
        diff = close.rolling(cond.fast).mean() - close.rolling(cond.slow).mean()
    elif isinstance(cond, PriceChangeCondition):
        change = close / close.shift(bars_for_hours(cond.lookback_hours, interval)) - 1
        return change <= -cond.pct / 100 if cond.direction == "down" else change >= cond.pct / 100  # NaN -> False
    else:  # pragma: no cover - Condition is a closed union
        raise TypeError(type(cond))
    if cond.op.startswith("crosses"):
        return _cross(diff, cond.op)
    return diff < 0 if cond.op == "below" else diff > 0  # comparisons with NaN (warm-up) are False


def signals(spec: StrategySpec, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    entry = [condition_series(c, df, spec.interval) for c in spec.entry]
    both = pd.concat(entry, axis=1)
    combined = both.all(axis=1) if spec.entry_logic == "all" else both.any(axis=1)
    exits = [condition_series(c, df, spec.interval) for c in spec.exit_signals]
    exit_any = pd.concat(exits, axis=1).any(axis=1) if exits else pd.Series(False, index=df.index)
    return combined, exit_any


# ------------------------------------------------------------------ simulation


def run_backtest(spec: StrategySpec, df: pd.DataFrame, *, source: str | None = None,
                 as_of: datetime | None = None) -> BacktestResult:
    if spec.position_size_pct > 100:
        raise BacktestError("Position size above 100% needs leverage, which isn't modelled.")
    conditions = list(spec.entry) + list(spec.exit_signals)
    warmup = max(lookback_bars(c, spec.interval) for c in conditions)  # first candle every indicator is defined on
    n = len(df)
    if warmup >= n - 1:
        raise BacktestError(f"The indicators need {warmup + 1} candles of history, but only {n} came back.")
    entry_sig, exit_sig = signals(spec, df)
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    times = list(df.index.to_pydatetime())
    fee, slip = spec.fee_pct / 100, spec.slippage_pct / 100
    hold_bars = bars_for_hours(spec.max_hold_hours, spec.interval) if spec.max_hold_hours else None

    cash = spec.demo_balance
    qty = notional = entry_px = 0.0
    entry_bar = -1
    pending: str | None = None  # "entry", "exit_signal" or "time_exit", filled at the next open
    trades: list[Trade] = []
    equity: list[float] = []
    fees_paid = 0.0
    both_touched = 0

    def close_trade(j: int, trigger_px: float, reason: ExitReason, at_open: bool = False) -> None:
        """Sell at `trigger_px` minus slippage. A fill at candle j's open means candle j wasn't held."""
        nonlocal cash, qty, fees_paid
        fill = trigger_px * (1 - slip)
        proceeds = qty * fill
        fee_out = proceeds * fee
        cash += proceeds - fee_out
        fees_paid += fee_out
        pnl = proceeds - fee_out - notional
        trades.append(Trade(
            entry_time=times[entry_bar], entry_price=round_price(entry_px), exit_time=times[j],
            exit_price=round_price(fill), exit_reason=reason,
            bars_held=max(1, j - entry_bar) if at_open else j - entry_bar + 1,
            notional=round(notional, 2), pnl=round(pnl, 2), return_pct=round(pnl / notional * 100, 4),
        ))
        qty = 0.0

    for j in range(warmup, n):
        if pending == "entry":
            entry_px = o[j] * (1 + slip)
            notional = cash * spec.position_size_pct / 100
            fees_paid += notional * fee
            qty = notional * (1 - fee) / entry_px
            cash -= notional
            entry_bar = j
        elif pending in ("exit_signal", "time_exit"):
            close_trade(j, o[j], pending, at_open=True)  # type: ignore[arg-type]
        pending = None

        if qty:  # resting take profit and stop loss
            stop = entry_px * (1 - spec.stop_loss_pct / 100) if spec.stop_loss_pct is not None else None
            target = entry_px * (1 + spec.take_profit_pct / 100) if spec.take_profit_pct is not None else None
            if stop is not None and o[j] <= stop:
                close_trade(j, o[j], "stop_loss", at_open=True)  # gapped through the stop
            elif target is not None and o[j] >= target:
                close_trade(j, o[j], "take_profit", at_open=True)  # gapped through the target
            elif stop is not None and lo[j] <= stop:
                both_touched += int(target is not None and h[j] >= target)
                close_trade(j, stop, "stop_loss")
            elif target is not None and h[j] >= target:
                close_trade(j, target, "take_profit")

        if j < n - 1:  # decisions at this close fill at the next open
            if qty:
                if exit_sig.iloc[j]:
                    pending = "exit_signal"
                elif hold_bars is not None and j - entry_bar + 1 >= hold_bars:
                    pending = "time_exit"
            elif entry_sig.iloc[j]:
                pending = "entry"
        equity.append(cash + qty * c[j])

    notes: list[str] = []
    if not qty and entry_sig.iloc[n - 1]:
        notes.append("The last candle closed with an entry signal; there was no next candle to fill it on.")
    if qty:
        close_trade(n - 1, c[n - 1], "end_of_data")
        equity[-1] = cash
        notes.append("One trade was still open on the last candle; it was closed at that candle's close.")
    if both_touched:
        notes.append(f"On {both_touched} candle(s) the high reached the take profit and the low reached the stop; "
                     "the stop was assumed to fill first.")

    eq = pd.Series(equity, dtype=float)
    drawdown = float((eq / eq.cummax() - 1).min() * 100)
    tradable = n - 1 - warmup
    returns = [t.return_pct for t in trades]
    bh_qty = (1 - fee) / (o[warmup + 1] * (1 + slip))  # buy-and-hold: 100% in at the first fill, out at the end
    bh_final = bh_qty * c[n - 1] * (1 - slip) * (1 - fee)
    metrics = Metrics(
        initial_balance=round(spec.demo_balance, 2),
        final_balance=round(cash, 2),
        total_return_pct=r2((cash / spec.demo_balance - 1) * 100) or 0.0,
        max_drawdown_pct=r2(drawdown) or 0.0,
        trade_count=len(trades),
        win_rate_pct=r2(sum(r > 0 for r in returns) / len(returns) * 100) if returns else None,
        avg_trade_pct=r2(float(np.mean(returns))) if returns else None,
        best_trade_pct=r2(max(returns)) if returns else None,
        worst_trade_pct=r2(min(returns)) if returns else None,
        exposure_pct=r2(sum(t.bars_held for t in trades) / tradable * 100) or 0.0,
        fees_paid=round(fees_paid, 2),
        buy_and_hold_return_pct=r2((bh_final - 1) * 100) or 0.0,
    )
    curve = [EquityPoint(time=times[warmup + i], equity=round(v, 2)) for i, v in enumerate(equity)]
    return BacktestResult(
        symbol=spec.symbol.upper(), interval=spec.interval, source=source, as_of=as_of,
        start=times[warmup], end=times[n - 1], candles=tradable, warmup_candles=warmup,
        metrics=metrics, trades=trades, equity_curve=curve, notes=notes,
    )
