"""Read-only market data: 24h ticker, candles and order book.

Binance public REST endpoints (no API key) are the primary source; the
data-api.binance.vision mirror is tried next, then CoinGecko for the assets it maps.
There is deliberately no order, account or trading endpoint anywhere in this module.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

import httpx
import pandas as pd
from pydantic import BaseModel, Field

from ..config import Settings
from ..http import UpstreamHTTPError, fetch
from ..numbers import r2, round_price
from ..symbols import COINGECKO_IDS, SymbolRegistry
from .errors import ToolError

log = logging.getLogger(__name__)

Interval = Literal["15m", "1h", "4h", "1d"]
INTERVAL_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}


# ------------------------------------------------------------------ schemas


class TickerInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)


class TickerOutput(BaseModel):
    symbol: str
    base_asset: str
    last_price: float
    open_price: float | None
    price_change: float
    price_change_pct: float
    high_24h: float
    low_24h: float
    volume_base: float | None
    volume_quote: float
    volume_note: str | None = None
    source: str
    as_of: datetime


class KlinesInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    interval: Interval = "1h"
    limit: int = Field(default=24, ge=1, le=500)


class Candle(BaseModel):
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    quote_volume: float | None


class KlinesOutput(BaseModel):
    symbol: str
    interval: Interval
    candles: list[Candle]
    source: str
    as_of: datetime


class OrderBookInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    depth: Literal[5, 10, 20, 50, 100] = 100


class BookLevel(BaseModel):
    price: float
    qty: float


class OrderBookOutput(BaseModel):
    symbol: str
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float
    spread_bps: float
    bid_depth_quote_1pct: float
    ask_depth_quote_1pct: float
    depth_imbalance_pct: float
    top_bids: list[BookLevel]
    top_asks: list[BookLevel]
    source: str
    as_of: datetime


# ------------------------------------------------------------------- client


class MarketData:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, registry: SymbolRegistry) -> None:
        self.settings = settings
        self.http = http
        self.registry = registry

    async def _get(self, url: str, params: dict | None = None):
        return await fetch(
            self.http, url, params, timeout=self.settings.tool_timeout_s, retries=self.settings.tool_retries
        )

    async def _binance(self, path: str, params: dict) -> tuple[object, str]:
        """Try each Binance base URL in turn. Returns (json, source label)."""
        last: ToolError | None = None
        for i, base in enumerate(self.settings.binance_base_urls):
            try:
                data = await self._get(f"{base}{path}", params)
                return data, "Binance public API" if i == 0 else "Binance public API (mirror)"
            except UpstreamHTTPError as exc:
                if isinstance(exc.body, dict) and exc.body.get("code") == -1121:
                    raise ToolError("unknown_symbol", f"The exchange doesn't list {params.get('symbol')}.")
                last = ToolError("upstream_error", f"The exchange returned HTTP {exc.status}.")
            except ToolError as exc:
                last = exc
            log.warning("binance %s failed via %s: %s", path, base, last.message if last else "")
        assert last is not None
        raise last

    # --------------------------------------------------------------- whitelist

    async def load_exchange_symbols(self) -> list[dict]:
        data, _ = await self._binance("/api/v3/exchangeInfo", {"permissions": "SPOT", "symbolStatus": "TRADING"})
        return [
            {"symbol": s["symbol"], "baseAsset": s["baseAsset"], "quoteAsset": s["quoteAsset"]}
            for s in data.get("symbols", [])  # type: ignore[union-attr]
            if s.get("status") == "TRADING"
        ]

    # ------------------------------------------------------------------ ticker

    async def get_ticker(self, pair: str) -> TickerOutput:
        try:
            raw, source = await self._binance("/api/v3/ticker/24hr", {"symbol": pair})
        except ToolError as exc:
            if exc.code == "unknown_symbol":
                raise
            return await self._coingecko_ticker(pair, exc)
        d = raw  # type: ignore[assignment]
        return TickerOutput(
            symbol=pair,
            base_asset=self.registry.base(pair),
            last_price=round_price(float(d["lastPrice"])),
            open_price=round_price(float(d["openPrice"])),
            price_change=round_price(float(d["priceChange"])),
            price_change_pct=r2(float(d["priceChangePercent"])),
            high_24h=round_price(float(d["highPrice"])),
            low_24h=round_price(float(d["lowPrice"])),
            volume_base=r2(float(d["volume"])),
            volume_quote=r2(float(d["quoteVolume"])),
            source=source,
            as_of=_ms(d["closeTime"]),
        )

    async def _coingecko_ticker(self, pair: str, cause: ToolError) -> TickerOutput:
        base = self.registry.base(pair)
        cg_id = COINGECKO_IDS.get(base)
        if not cg_id:
            raise cause
        log.warning("falling back to CoinGecko for %s after: %s", pair, cause.message)
        rows = await self._get(
            f"{self.settings.coingecko_base_url}/coins/markets", {"vs_currency": "usd", "ids": cg_id}
        )
        if not rows:
            raise ToolError("empty_data", f"No backup data available for {base}.")
        d = rows[0]
        last = float(d["current_price"])
        change = float(d.get("price_change_24h") or 0.0)
        return TickerOutput(
            symbol=pair,
            base_asset=base,
            last_price=round_price(last),
            open_price=round_price(last - change),
            price_change=round_price(change),
            price_change_pct=r2(float(d.get("price_change_percentage_24h") or 0.0)),
            high_24h=round_price(float(d["high_24h"])),
            low_24h=round_price(float(d["low_24h"])),
            volume_base=None,
            volume_quote=r2(float(d["total_volume"])),
            volume_note="USD volume aggregated across exchanges (CoinGecko), not Binance-only",
            source="CoinGecko API (backup)",
            as_of=_parse_iso(d.get("last_updated")),
        )

    # ------------------------------------------------------------------ klines

    async def get_klines(self, pair: str, interval: Interval, limit: int) -> KlinesOutput:
        try:
            raw, source = await self._binance(
                "/api/v3/klines", {"symbol": pair, "interval": interval, "limit": limit}
            )
        except ToolError as exc:
            if exc.code == "unknown_symbol":
                raise
            return await self._coingecko_klines(pair, interval, limit, exc)
        candles = [
            Candle(
                open_time=_ms(k[0]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
                quote_volume=float(k[7]),
            )
            for k in raw  # type: ignore[union-attr]
        ]
        if not candles:
            raise ToolError("empty_data", f"The exchange returned no candles for {pair}.")
        return KlinesOutput(
            symbol=pair, interval=interval, candles=candles, source=source,
            as_of=min(_ms(raw[-1][6]), datetime.now(UTC)),  # type: ignore[index]  # last candle may still be open
        )

    async def _coingecko_klines(self, pair: str, interval: Interval, limit: int, cause: ToolError) -> KlinesOutput:
        base = self.registry.base(pair)
        cg_id = COINGECKO_IDS.get(base)
        if not cg_id:
            raise cause
        minutes = INTERVAL_MINUTES[interval] * limit
        days = max(2, min(90, -(-minutes // 1440) + 1))  # hourly points need 2..90 days
        data = await self._get(
            f"{self.settings.coingecko_base_url}/coins/{cg_id}/market_chart",
            {"vs_currency": "usd", "days": days},
        )
        prices = data.get("prices") or []
        if not prices:
            raise ToolError("empty_data", f"No backup candle data available for {base}.")
        s = pd.Series([p[1] for p in prices], index=pd.to_datetime([p[0] for p in prices], unit="ms", utc=True))
        rule = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}[interval]
        ohlc = s.resample(rule).ohlc().dropna().tail(limit)
        candles = [
            Candle(open_time=ts.to_pydatetime(), open=r.open, high=r.high, low=r.low, close=r.close,
                   volume=None, quote_volume=None)
            for ts, r in ohlc.iterrows()
        ]
        return KlinesOutput(
            symbol=pair, interval=interval, candles=candles, source="CoinGecko API (backup, no volume)",
            as_of=s.index[-1].to_pydatetime(),
        )

    # -------------------------------------------------------------- order book

    async def get_order_book(self, pair: str, depth: int) -> OrderBookOutput:
        raw, source = await self._binance("/api/v3/depth", {"symbol": pair, "limit": depth})
        bids = [(float(p), float(q)) for p, q in raw.get("bids", [])]  # type: ignore[union-attr]
        asks = [(float(p), float(q)) for p, q in raw.get("asks", [])]  # type: ignore[union-attr]
        if not bids or not asks:
            raise ToolError("empty_data", f"The order book for {pair} is empty right now.")
        best_bid, best_ask = bids[0][0], asks[0][0]
        mid = (best_bid + best_ask) / 2
        bid_depth = sum(p * q for p, q in bids if p >= mid * 0.99)
        ask_depth = sum(p * q for p, q in asks if p <= mid * 1.01)
        total = bid_depth + ask_depth
        return OrderBookOutput(
            symbol=pair,
            best_bid=round_price(best_bid),
            best_ask=round_price(best_ask),
            mid_price=round_price(mid),
            spread=round_price(best_ask - best_bid) if best_ask - best_bid >= 1 else round(best_ask - best_bid, 8),
            spread_bps=round((best_ask - best_bid) / mid * 10_000, 3),
            bid_depth_quote_1pct=r2(bid_depth),
            ask_depth_quote_1pct=r2(ask_depth),
            depth_imbalance_pct=r2((bid_depth - ask_depth) / total * 100) if total else 0.0,
            top_bids=[BookLevel(price=p, qty=q) for p, q in bids[:5]],
            top_asks=[BookLevel(price=p, qty=q) for p, q in asks[:5]],
            source=source,
            as_of=datetime.now(UTC),
        )


def _ms(value: int | float) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
