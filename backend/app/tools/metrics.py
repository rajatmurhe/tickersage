"""Deterministic indicators computed in Pandas. The LLM never does arithmetic."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from ..numbers import r2, round_price
from ..schemas import Window
from .errors import ToolError
from .market import Interval, KlinesOutput, MarketData

RSI_PERIOD = 14
WARMUP = 50  # extra candles so RSI(14) and SMA(50) are defined at the window start

# window -> (candle interval, candles inside the window, periods per year for annualising)
WINDOW_SPEC: dict[str, tuple[Interval, int, float]] = {
    "24h": ("1h", 24, 24 * 365),
    "7d": ("4h", 42, 6 * 365),
    "30d": ("1d", 30, 365),
}


class MetricsInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    window: Window = "7d"


class MetricsOutput(BaseModel):
    symbol: str
    base_asset: str
    window: Window
    interval: Interval
    n_candles: int
    start_price: float
    end_price: float
    return_pct: float
    high: float
    low: float
    range_pct: float
    volatility_pct: float | None = Field(description="Std dev of per-candle returns, in percent")
    annualized_volatility_pct: float | None
    max_drawdown_pct: float
    rsi_14: float | None
    rsi_overbought: int = 70
    rsi_oversold: int = 30
    sma_20: float | None
    sma_50: float | None
    price_vs_sma_20_pct: float | None
    vwap: float | None
    price_vs_vwap_pct: float | None
    volume_base: float | None
    volume_quote: float | None
    source: str
    as_of: datetime


class CompareInput(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=5)
    window: Window = "7d"


class CompareRow(BaseModel):
    symbol: str
    base_asset: str
    end_price: float
    return_pct: float
    volatility_pct: float | None
    annualized_volatility_pct: float | None
    max_drawdown_pct: float
    rsi_14: float | None
    volume_quote: float | None


class CompareOutput(BaseModel):
    window: Window
    rows: list[CompareRow]
    highest_return: str
    lowest_return: str
    highest_volatility: str | None
    highest_volume: str | None
    volume_ratio: float | None = Field(description="First asset's quote volume divided by the second's")
    volatility_ratio: float | None = Field(description="First asset's volatility divided by the second's")
    return_gap_pct: float = Field(description="First asset's return minus the second's, in percentage points")
    skipped: list[str] = Field(default_factory=list, description="Requested symbols that could not be found")
    source: str
    as_of: datetime


def candles_frame(klines: KlinesOutput) -> pd.DataFrame:
    df = pd.DataFrame([c.model_dump() for c in klines.candles])
    if df.empty:
        raise ToolError("empty_data", f"No candle data for {klines.symbol}.")
    return df.set_index("open_time").sort_index()


def rsi(close: pd.Series, period: int = RSI_PERIOD) -> float | None:
    """Wilder's RSI on the last candle."""
    if len(close) <= period:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().iloc[-1]
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def metrics_from_frame(
    df: pd.DataFrame,
    *,
    symbol: str,
    base_asset: str,
    window: Window,
    interval: Interval,
    n_window: int,
    periods_per_year: float,
    source: str,
    as_of: datetime,
) -> MetricsOutput:
    full = df
    win = df.tail(n_window)
    if len(win) < 2:
        raise ToolError("empty_data", f"Not enough candles to compute metrics for {symbol}.")
    close = full["close"].astype(float)
    wclose = win["close"].astype(float)

    start, end = float(win["open"].iloc[0]), float(wclose.iloc[-1])
    high, low = float(win["high"].max()), float(win["low"].min())
    rets = np.log(wclose / wclose.shift(1)).dropna()
    vol = float(rets.std(ddof=1)) if len(rets) > 1 else None
    running_max = wclose.cummax()
    drawdown = float(((wclose / running_max) - 1).min()) * 100

    sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None

    rsi_14 = rsi(close)
    vwap = None
    has_volume = "volume" in win and win["volume"].notna().all() and float(win["volume"].sum()) > 0
    if has_volume:
        typical = (win["high"] + win["low"] + win["close"]) / 3
        vwap = float((typical * win["volume"]).sum() / win["volume"].sum())

    return MetricsOutput(
        symbol=symbol,
        base_asset=base_asset,
        window=window,
        interval=interval,
        n_candles=len(win),
        start_price=round_price(start),
        end_price=round_price(end),
        return_pct=r2((end / start - 1) * 100),
        high=round_price(high),
        low=round_price(low),
        range_pct=r2((high / low - 1) * 100),
        volatility_pct=r2(vol * 100) if vol is not None else None,
        annualized_volatility_pct=r2(vol * math.sqrt(periods_per_year) * 100) if vol is not None else None,
        max_drawdown_pct=r2(drawdown),
        rsi_14=r2(rsi_14),
        sma_20=round_price(sma20) if sma20 is not None else None,
        sma_50=round_price(sma50) if sma50 is not None else None,
        price_vs_sma_20_pct=r2((end / sma20 - 1) * 100) if sma20 else None,
        vwap=round_price(vwap) if vwap is not None else None,
        price_vs_vwap_pct=r2((end / vwap - 1) * 100) if vwap else None,
        volume_base=r2(float(win["volume"].sum())) if has_volume else None,
        volume_quote=r2(float(win["quote_volume"].sum())) if has_volume and win["quote_volume"].notna().all() else None,
        source=source,
        as_of=as_of,
    )


async def compute_metrics(market: MarketData, pair: str, window: Window) -> MetricsOutput:
    interval, n_window, ppy = WINDOW_SPEC[window]
    klines = await market.get_klines(pair, interval, n_window + WARMUP)
    df = candles_frame(klines)
    return metrics_from_frame(
        df,
        symbol=pair,
        base_asset=market.registry.base(pair),
        window=window,
        interval=interval,
        n_window=n_window,
        periods_per_year=ppy,
        source=klines.source,
        as_of=klines.as_of,
    )


async def compare_assets(market: MarketData, pairs: list[str], window: Window) -> CompareOutput:
    results = await asyncio.gather(*(compute_metrics(market, p, window) for p in pairs), return_exceptions=True)
    rows: list[MetricsOutput] = []
    for pair, res in zip(pairs, results, strict=True):
        if isinstance(res, ToolError):
            raise ToolError(res.code, f"{pair}: {res.message}")
        if isinstance(res, BaseException):
            raise res
        rows.append(res)
    return build_comparison(rows, window)


def build_comparison(rows: list[MetricsOutput], window: Window) -> CompareOutput:
    def best(key: str, reverse: bool = True) -> str | None:
        valid = [r for r in rows if getattr(r, key) is not None]
        if not valid:
            return None
        pick = max(valid, key=lambda r: getattr(r, key)) if reverse else min(valid, key=lambda r: getattr(r, key))
        return pick.base_asset

    a, b = rows[0], rows[1]
    vol_ratio = (
        round(a.volume_quote / b.volume_quote, 2) if a.volume_quote and b.volume_quote else None
    )
    volat_ratio = (
        round(a.volatility_pct / b.volatility_pct, 2) if a.volatility_pct and b.volatility_pct else None
    )
    return CompareOutput(
        window=window,
        rows=[CompareRow(**r.model_dump(include=set(CompareRow.model_fields))) for r in rows],
        highest_return=best("return_pct") or a.base_asset,
        lowest_return=best("return_pct", reverse=False) or a.base_asset,
        highest_volatility=best("volatility_pct"),
        highest_volume=best("volume_quote"),
        volume_ratio=vol_ratio,
        volatility_ratio=volat_ratio,
        return_gap_pct=round(a.return_pct - b.return_pct, 2),
        source=rows[0].source,
        as_of=max(r.as_of for r in rows),
    )

