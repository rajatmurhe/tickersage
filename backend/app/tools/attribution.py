"""Why did it move? A deterministic evidence pack, computed in Pandas.

`attribute_move(symbol, window)` splits a move into a market-implied part (the coin's 30-day beta to BTC
times BTC's move over the same window) and a coin-specific residual, then checks four more factors:
volume against its trailing average, order-book imbalance, a shift in headline sentiment and the
perpetual funding rate. A factor is listed in `drivers` only when it crosses its threshold below. When
none does, `no_clear_driver` is set and the answer says so instead of guessing.

The pack re-uses compute_metrics, get_order_book and the get_news_sentiment feed through the toolbox, so
the cache, replay fixtures, injected faults and poisoned overrides apply to those parts as they do to a
top-level call. Its own inputs (daily candles and funding history) are fetched up to the coin's metrics
snapshot. Record mode saves them as a `raw_inputs` fixture, so replay re-runs this maths instead of
serving a stored answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from ..cache import TTL_KLINES, TTL_NEWS
from ..http import UpstreamHTTPError
from ..numbers import r2
from ..schemas import Window
from ..symbols import asset_name
from .errors import ToolError
from .market import MarketData
from .news import SOURCE as NEWS_SOURCE
from .news import NewsInput, build_news_output

if TYPE_CHECKING:
    from .registry import RunOptions, Toolbox

log = logging.getLogger(__name__)

# ----------------------------------------------------------------- thresholds
# A factor counts as a driver only when it crosses its threshold. They are plain constants (and are
# returned with every pack) so it's clear exactly what "unusual" means.
MIN_MOVE_PCT = 0.5  # a smaller move is noise: nothing is attributed and no_clear_driver is set
MARKET_SHARE_MIN = 0.5  # market-wide: beta x BTC's move is at least half the move, in the same direction,
MARKET_MOVE_MIN_PCT = 1.0  # and at least 1 percentage point
VOLUME_Z_MIN = 2.0  # volume: average daily volume in the window >= 2 std devs above the 30 days before it
BOOK_IMBALANCE_MIN_PCT = 20.0  # order book: depth near the mid tilted >= 20% toward the side the price moved
NEWS_SHIFT_MIN = 0.1  # news: average VADER score, last 24h vs the 24h before, moved >= 0.1 the way price did
NEWS_MIN_HEADLINES = 5  # headlines needed in each 24h half before a shift is measured
FUNDING_CHANGE_MIN_PCT = 0.03  # funding: the rate per interval changed >= 0.03 points over the window,
FUNDING_EXTREME_PCT = 0.05  # or sits at >= 0.05% per interval either way (5x Binance's 0.01% default)

THRESHOLDS: dict[str, float] = {
    "min_move_pct": MIN_MOVE_PCT,
    "market_share_min": MARKET_SHARE_MIN,
    "market_move_min_pct": MARKET_MOVE_MIN_PCT,
    "volume_z_min": VOLUME_Z_MIN,
    "book_imbalance_min_pct": BOOK_IMBALANCE_MIN_PCT,
    "news_shift_min": NEWS_SHIFT_MIN,
    "funding_change_min_pct": FUNDING_CHANGE_MIN_PCT,
    "funding_extreme_pct": FUNDING_EXTREME_PCT,
}

BENCHMARK = "BTCUSDT"
BETA_DAYS = 30
MIN_BETA_DAYS = 20
VOLUME_BASELINE_DAYS = 30
MIN_BASELINE_DAYS = 10
WINDOW_DAYS: dict[str, int] = {"24h": 1, "7d": 7, "30d": 30}
NEWS_HALF = timedelta(hours=24)

DriverName = Literal["market_wide", "volume", "order_book", "news", "funding"]
DRIVERS: tuple[DriverName, ...] = ("market_wide", "volume", "order_book", "news", "funding")


# -------------------------------------------------------------------- schemas


class AttributeMoveInput(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    window: Window = "24h"


class Factor(BaseModel):
    available: bool = True
    flagged: bool = False
    note: str | None = None


class MarketFactor(Factor):
    benchmark: str = "BTC"
    btc_move_pct: float | None = None
    beta_vs_btc: float | None = None
    correlation_vs_btc: float | None = None
    beta_days: int = Field(default=0, description="Daily returns the beta was estimated on")


class VolumeFactor(Factor):
    z_score: float | None = None
    window_avg_daily_volume: float | None = Field(default=None, description="Quote volume per day in the window")
    baseline_avg_daily_volume: float | None = Field(default=None, description="Per day, before the window")
    baseline_days: int = 0


class OrderBookFactor(Factor):
    imbalance_pct: float | None = Field(default=None, description="(bid - ask depth) / total within 1% of mid")
    heavier_side: Literal["bids", "asks"] | None = None


class NewsFactor(Factor):
    recent_avg_sentiment: float | None = Field(default=None, description="Headlines from the last 24h")
    earlier_avg_sentiment: float | None = Field(default=None, description="Headlines from the 24h before")
    sentiment_shift: float | None = None
    recent_headlines: int = 0
    earlier_headlines: int = 0


class FundingFactor(Factor):
    funding_rate_pct: float | None = Field(default=None, description="Latest settled rate, % per interval")
    funding_change_pct: float | None = Field(default=None, description="Change over the window, in points")
    interval_hours: int | None = None


class AttributionOutput(BaseModel):
    symbol: str
    base_asset: str
    window: Window
    move_pct: float = Field(description="The coin's return over the window")
    market_implied_pct: float | None = Field(description="beta_vs_btc x BTC's move over the window")
    residual_pct: float | None = Field(description="move_pct minus market_implied_pct: the coin-specific part")
    small_move: bool
    market_wide: MarketFactor
    volume: VolumeFactor
    order_book: OrderBookFactor
    news: NewsFactor
    funding: FundingFactor
    drivers: list[DriverName] = Field(description="Factors that crossed their threshold: the only causes to name")
    no_clear_driver: bool
    thresholds: dict[str, float]
    source: str
    as_of: datetime


# ---------------------------------------------------------------------- maths


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _time(value: str | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def daily_closes(candles: list[dict], end: datetime) -> pd.Series:
    """Closes of the daily candles that had closed by `end`, indexed by day."""
    if not candles:
        return pd.Series(dtype=float)
    index = pd.DatetimeIndex([_time(c["open_time"]) for c in candles])
    s = pd.Series([float(c["close"]) for c in candles], index=index).sort_index()
    return s[s.index + pd.Timedelta(days=1) <= pd.Timestamp(end)]


def beta_vs_benchmark(coin: pd.Series, bench: pd.Series, days: int = BETA_DAYS
                      ) -> tuple[float | None, float | None, int]:
    """(beta, correlation, n) of daily returns over the last `days` days both series share.

    beta = cov(coin, bench) / var(bench), the OLS slope of the coin's daily return on the benchmark's.
    """
    rets = pd.concat([coin.pct_change(), bench.pct_change()], axis=1, join="inner").dropna().tail(days)
    n = len(rets)
    if n < MIN_BETA_DAYS:
        return None, None, n
    c, b = rets.iloc[:, 0], rets.iloc[:, 1]
    var = float(b.var(ddof=1))
    if not var > 0:
        return None, None, n
    return float(c.cov(b, ddof=1)) / var, float(c.corr(b)), n


def volume_zscore(window_volume: float | None, window_days: int, candles: list[dict], window_start: datetime
                  ) -> tuple[float | None, float | None, float | None, int]:
    """(z, window avg/day, baseline avg/day, baseline days): the window's average daily quote volume against
    the daily volumes of the VOLUME_BASELINE_DAYS full days before the window started."""
    base = [
        float(c["quote_volume"]) for c in sorted(candles, key=lambda c: _time(c["open_time"]))
        if c.get("quote_volume") is not None and _time(c["open_time"]) + timedelta(days=1) <= window_start
    ][-VOLUME_BASELINE_DAYS:]
    if window_volume is None or len(base) < MIN_BASELINE_DAYS:
        return None, None, None, len(base)
    avg_w, mean, sd = window_volume / window_days, statistics.fmean(base), statistics.stdev(base)
    z = (avg_w - mean) / sd if sd > 0 else None
    return z, avg_w, mean, len(base)


def news_shift(headlines: list[dict], end: datetime) -> tuple[float | None, float | None, float | None, int, int]:
    """(recent avg, earlier avg, shift, n recent, n earlier): VADER scores of the last 24h of headlines
    against the 24h before. Withheld (redacted) headlines are left out."""
    recent: list[float] = []
    earlier: list[float] = []
    for h in headlines:
        if h.get("redacted") or not h.get("published_at"):
            continue
        age = end - _time(h["published_at"])
        if age < NEWS_HALF:
            recent.append(float(h["sentiment"]))
        elif age < 2 * NEWS_HALF:
            earlier.append(float(h["sentiment"]))
    avg_r = round(statistics.fmean(recent), 3) if recent else None
    avg_e = round(statistics.fmean(earlier), 3) if earlier else None
    enough = len(recent) >= NEWS_MIN_HEADLINES and len(earlier) >= NEWS_MIN_HEADLINES
    shift = round(avg_r - avg_e, 3) if enough and avg_r is not None and avg_e is not None else None
    return avg_r, avg_e, shift, len(recent), len(earlier)


def funding_summary(rows: list[dict], window_start: datetime) -> tuple[float, float, int | None]:
    """(latest rate %, change over the window in points, interval hours) from settled funding rows."""
    rows = sorted(rows, key=lambda r: r["time"])
    start_ms = window_start.timestamp() * 1000
    before = [r for r in rows if r["time"] <= start_ms]
    ref = before[-1] if before else rows[0]
    rate, ref_rate = round(rows[-1]["rate"] * 100, 4), round(ref["rate"] * 100, 4)
    gaps = [b["time"] - a["time"] for a, b in zip(rows, rows[1:], strict=False)]
    interval = round(statistics.median(gaps) / 3_600_000) if gaps else None
    return rate, round(rate - ref_rate, 4), interval


def build_attribution(
    *,
    symbol: str,
    base_asset: str,
    window: Window,
    coin_metrics: dict[str, Any],
    btc_metrics: dict[str, Any] | ToolError | None,
    book: dict[str, Any] | ToolError,
    headlines: list[dict] | ToolError,
    raw: dict[str, Any],
) -> AttributionOutput:
    """The evidence pack. Pure: everything it needs is passed in, so tests can drive it directly."""
    end = _time(coin_metrics["as_of"])
    days = WINDOW_DAYS[window]
    window_start = end - timedelta(days=days)
    move = float(coin_metrics["return_pct"])
    small = abs(move) < MIN_MOVE_PCT
    coin_daily, btc_daily = raw.get("coin_daily") or {}, raw.get("btc_daily") or {}

    # market vs coin-specific
    market = MarketFactor()
    implied = residual = None
    if symbol == BENCHMARK:
        market = MarketFactor(available=False, note="BTC is the benchmark, so its move isn't split into a "
                                                     "market part and a coin-specific part.")
    elif isinstance(btc_metrics, ToolError) or btc_metrics is None:
        msg = btc_metrics.message if isinstance(btc_metrics, ToolError) else "no data"
        market = MarketFactor(available=False, note=f"BTC's move over the window is unavailable: {msg}")
    elif "error" in coin_daily or "error" in btc_daily:
        market = MarketFactor(available=False, btc_move_pct=btc_metrics["return_pct"],
                              note=f"Daily candles for the beta are unavailable: "
                                   f"{coin_daily.get('error') or btc_daily.get('error')}")
    else:
        beta, corr, n = beta_vs_benchmark(daily_closes(coin_daily.get("candles", []), end),
                                          daily_closes(btc_daily.get("candles", []), end))
        btc_move = float(btc_metrics["return_pct"])
        if beta is None:
            market = MarketFactor(available=False, btc_move_pct=btc_move, beta_days=n,
                                  note=f"Only {n} days of shared daily history; the beta needs {MIN_BETA_DAYS}.")
        else:
            implied = r2(beta * btc_move)
            residual = r2(move - implied)
            market = MarketFactor(
                btc_move_pct=btc_move, beta_vs_btc=r2(beta), correlation_vs_btc=r2(corr), beta_days=n,
                flagged=not small and _sign(implied) == _sign(move)
                and abs(implied) >= MARKET_SHARE_MIN * abs(move) and abs(implied) >= MARKET_MOVE_MIN_PCT,
            )

    # volume
    if "error" in coin_daily:
        volume = VolumeFactor(available=False, note=f"Daily volume history is unavailable: {coin_daily['error']}")
    else:
        z, avg_w, avg_b, n = volume_zscore(coin_metrics.get("volume_quote"), days, coin_daily.get("candles", []),
                                           window_start)
        if z is None:
            volume = VolumeFactor(available=False, baseline_days=n,
                                  note="Not enough volume history to compare the window with.")
        else:
            volume = VolumeFactor(z_score=r2(z), window_avg_daily_volume=r2(avg_w),
                                  baseline_avg_daily_volume=r2(avg_b), baseline_days=n,
                                  flagged=not small and r2(z) >= VOLUME_Z_MIN)

    # order book (a snapshot now, so it shows where pressure sits, not what happened earlier)
    if isinstance(book, ToolError):
        order_book = OrderBookFactor(available=False, note=f"The order book is unavailable: {book.message}")
    else:
        imb = float(book["depth_imbalance_pct"])
        order_book = OrderBookFactor(
            imbalance_pct=imb, heavier_side="bids" if imb > 0 else "asks" if imb < 0 else None,
            flagged=not small and abs(imb) >= BOOK_IMBALANCE_MIN_PCT and _sign(imb) == _sign(move),
        )

    # news
    if isinstance(headlines, ToolError):
        news = NewsFactor(available=False, note=f"Headlines are unavailable: {headlines.message}")
    else:
        avg_r, avg_e, shift, n_r, n_e = news_shift(headlines, end)
        news = NewsFactor(
            recent_avg_sentiment=avg_r, earlier_avg_sentiment=avg_e, sentiment_shift=shift,
            recent_headlines=n_r, earlier_headlines=n_e,
            note=None if shift is not None
            else f"Too few headlines to measure a shift (needs {NEWS_MIN_HEADLINES} in each 24-hour half).",
            flagged=not small and shift is not None and abs(shift) >= NEWS_SHIFT_MIN
            and _sign(shift) == _sign(move),
        )

    # funding
    fund = raw.get("funding") or {"error": "Funding data unavailable."}
    if "error" in fund:
        funding = FundingFactor(available=False, note=fund["error"])
    else:
        rate, change, interval = funding_summary(fund["rows"], window_start)
        funding = FundingFactor(
            funding_rate_pct=rate, funding_change_pct=change, interval_hours=interval,
            flagged=not small and (abs(change) >= FUNDING_CHANGE_MIN_PCT or abs(rate) >= FUNDING_EXTREME_PCT),
        )

    factors = {"market_wide": market, "volume": volume, "order_book": order_book, "news": news,
               "funding": funding}
    drivers = [name for name in DRIVERS if factors[name].flagged]
    return AttributionOutput(
        symbol=symbol, base_asset=base_asset, window=window, move_pct=move,
        market_implied_pct=implied, residual_pct=residual, small_move=small,
        market_wide=market, volume=volume, order_book=order_book, news=news, funding=funding,
        drivers=drivers, no_clear_driver=not drivers, thresholds=dict(THRESHOLDS),
        source=coin_metrics.get("source") or "Binance public API", as_of=end,
    )


# ------------------------------------------------------------ fetching inputs


async def fetch_raw_inputs(market: MarketData, pair: str, window: str, end: datetime) -> dict[str, Any]:
    """Daily candles (coin and BTC) and funding history up to `end`. Each part fails soft on its own."""
    days = WINDOW_DAYS[window]
    coin_limit = max(BETA_DAYS, days + VOLUME_BASELINE_DAYS) + 2  # +1 return, +1 for the unfinished day
    coin, btc, funding = await asyncio.gather(
        _daily(market, pair, coin_limit, end),
        _daily(market, BENCHMARK, BETA_DAYS + 2, end) if pair != BENCHMARK else _nothing(),
        _funding(market, pair, end - timedelta(days=days + 1), end),
    )
    return {"end": end.isoformat(), "coin_daily": coin, "btc_daily": btc, "funding": funding}


async def _nothing() -> None:
    return None


async def _daily(market: MarketData, pair: str, limit: int, end: datetime) -> dict[str, Any]:
    try:
        k = await market.get_klines(pair, "1d", limit, end=end)
    except ToolError as exc:
        return {"error": exc.message}
    return {
        "source": k.source,
        "candles": [{"open_time": c.open_time.isoformat(), "close": c.close, "quote_volume": c.quote_volume}
                    for c in k.candles],
    }


async def _funding(market: MarketData, pair: str, start: datetime, end: datetime) -> dict[str, Any]:
    try:
        rows = await market.get_funding_history(pair, start, end)
    except UpstreamHTTPError as exc:
        if exc.status in (403, 451):
            why = f"Binance's futures API is blocked from this server's region (HTTP {exc.status})"
        elif exc.status == 400:
            why = f"Binance has no perpetual futures contract for {pair}"
        else:
            why = f"Binance's futures API returned HTTP {exc.status}"
        return {"error": f"Funding data unavailable: {why}."}
    except ToolError as exc:
        return {"error": f"Funding data unavailable: {exc.message}"}
    except (KeyError, TypeError, ValueError):
        return {"error": "Funding data unavailable: the futures API returned something unexpected."}
    if not rows:
        return {"error": f"Funding data unavailable: Binance lists no perpetual futures funding for {pair}."}
    return {"rows": rows}


# ----------------------------------------------------- running inside the toolbox


async def attribute_move(tb: Toolbox, args: dict[str, Any], opts: RunOptions) -> dict[str, Any]:
    pair, window = args["symbol"], args["window"]
    base = tb.registry.base(pair)
    news_args = tb._normalize("get_news_sentiment", NewsInput(query=asset_name(base)))
    coin, btc, book, headlines = await asyncio.gather(
        _sub(tb, "compute_metrics", {"symbol": pair, "window": window}, opts),
        _sub(tb, "compute_metrics", {"symbol": BENCHMARK, "window": window}, opts) if pair != BENCHMARK
        else _nothing(),
        _sub(tb, "get_order_book", {"symbol": pair, "depth": 100}, opts),
        _feed(tb, news_args, opts),
    )
    if isinstance(coin, ToolError):
        raise ToolError(coin.code, f"Can't attribute {base}'s move without its price data: {coin.message}")
    end = _time(coin["as_of"])
    raw = await _raw_inputs(tb, args, opts, lambda: fetch_raw_inputs(tb.market, pair, window, end))
    out = build_attribution(symbol=pair, base_asset=base, window=window, coin_metrics=coin, btc_metrics=btc,
                            book=book, headlines=headlines, raw=raw)
    return out.model_dump(mode="json")


def _check_fault(tool: str, opts: RunOptions) -> None:
    from .registry import _FAULT_MESSAGES

    if tool in opts.faults:
        code = opts.faults[tool]
        raise ToolError(code, _FAULT_MESSAGES.get(code, "The data source failed."))  # type: ignore[arg-type]


async def _sub(tb: Toolbox, tool: str, args: dict[str, Any], opts: RunOptions) -> dict[str, Any] | ToolError:
    """A tool call made for the pack: faults, overrides, replay and the cache apply as for a top-level call.
    In record mode an existing fixture is reused, so recording the pack never re-records another tool."""
    try:
        _check_fault(tool, opts)
        if tb.mode == "record" and tool not in opts.overrides and tb._fixture_path(tool, args).exists():
            return tb._from_fixture(tool, args, tb._load(tb._fixture_path(tool, args)))
        data, _ = await tb._run_inner(tool, args, opts)
        return data
    except ToolError as exc:
        return exc
    except Exception:
        log.exception("attribute_move: %s failed", tool)
        return ToolError("upstream_error", "The data source returned something unexpected.")


async def _feed(tb: Toolbox, args: dict[str, Any], opts: RunOptions) -> list[dict] | ToolError:
    """Every scored headline in the 2-day news feed. get_news_sentiment keeps only the newest few, too few
    to compare the last 24 hours with the 24 before, so this reads its feed (same fixture, fault and
    override) in full."""
    tool = "get_news_sentiment"
    try:
        _check_fault(tool, opts)
        override = opts.overrides.get(tool)
        path = Path(override) if override else tb._fixture_path(tool, args)
        if override or tb.mode == "replay" or (tb.mode == "record" and path.exists()):
            fx = tb._load(path)
            if fx.get("kind") == "error":
                raise ToolError(fx["data"]["code"], fx["data"]["message"])
            if fx.get("kind") != "raw_items":
                raise ToolError("empty_data", "No full headline feed was recorded.")
            items = [dict(i) for i in fx["data"]]
        else:
            key = f"tool:news_feed:{args['query']}"
            items = await tb.cache.get(key)
            if items is None:
                items = await tb.news.fetch_items(args["query"])
                await tb.cache.set(key, items, TTL_NEWS)
                if tb.mode == "record":
                    tb._save(tool, args, "raw_items", items)
        if not items:
            raise ToolError("empty_data", f"No recent headlines found for '{args['query']}'.")
        for item in items:
            if isinstance(item.get("published_at"), str):
                item["published_at"] = datetime.fromisoformat(item["published_at"])
        scored = build_news_output(args["query"], items, len(items), NEWS_SOURCE)
        return [h.model_dump() for h in scored.headlines]
    except ToolError as exc:
        return exc
    except Exception:
        log.exception("attribute_move: news feed failed")
        return ToolError("upstream_error", "The news source returned something unexpected.")


async def _raw_inputs(tb: Toolbox, args: dict[str, Any], opts: RunOptions,
                      fetch: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    override = opts.overrides.get("attribute_move")
    path = Path(override) if override else tb._fixture_path("attribute_move", args)
    if override or tb.mode == "replay" or (tb.mode == "record" and path.exists()):
        fx = tb._load(path)
        if fx.get("kind") == "error":
            raise ToolError(fx["data"]["code"], fx["data"]["message"])
        return fx["data"]
    key = f"tool:attribute_move:raw:{json.dumps(args, sort_keys=True)}"
    hit = await tb.cache.get(key)
    if hit is not None:
        return hit
    data = await fetch()
    await tb.cache.set(key, data, TTL_KLINES)
    if tb.mode == "record":
        tb._save("attribute_move", args, "raw_inputs", data)
    return data
