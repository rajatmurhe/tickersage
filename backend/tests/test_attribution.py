import itertools
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import pytest
import respx

from app.config import Settings
from app.schemas import ToolCall
from app.symbols import SymbolRegistry
from app.templates import attribution_block
from app.tools.attribution import (
    BOOK_IMBALANCE_MIN_PCT, DRIVERS, FUNDING_CHANGE_MIN_PCT, FUNDING_EXTREME_PCT, MIN_MOVE_PCT, NEWS_SHIFT_MIN,
    VOLUME_Z_MIN, beta_vs_benchmark, build_attribution, daily_closes, fetch_raw_inputs, funding_summary,
    news_shift, volume_zscore,
)
from app.tools.errors import ToolError
from app.tools.market import MarketData
from app.tools.registry import RunOptions
from evals.causes import named_causes, unsupported_causes

END = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)
TODAY = END.replace(hour=0)
BASELINE = [90.0, 110.0] * 21  # mean 100; the last 30 days have sample std dev 10.171
BASE_SD = float(np.std([90.0, 110.0] * 15, ddof=1))


def daily(closes, volumes=None):
    """Daily candles ending with the unfinished day that contains END."""
    n = len(closes)
    vols = volumes or [100.0] * n
    return [{"open_time": (TODAY - timedelta(days=n - 1 - i)).isoformat(), "close": c, "quote_volume": v}
            for i, (c, v) in enumerate(zip(closes, vols, strict=True))]


def closes_from(returns, start=100.0):
    out = [start]
    for r in returns:
        out.append(out[-1] * (1 + r))
    return out


BTC_RETS = np.random.default_rng(7).normal(0, 0.02, 40)


def headlines(recent, earlier):
    hs = [{"sentiment": s, "published_at": END - timedelta(hours=2), "redacted": False} for s in recent]
    return hs + [{"sentiment": s, "published_at": END - timedelta(hours=30), "redacted": False} for s in earlier]


def pack(*, move=-3.0, btc_move=-0.2, beta=1.0, z=0.0, imbalance=0.0, news=(0.0, 0.0), funding=(0.01, 0.01),
         symbol="ETHUSDT", btc_metrics=None, book=None, feed=None, raw_funding=None):
    """A pack where each factor is set directly; with the defaults nothing crosses a threshold."""
    btc_closes = closes_from(BTC_RETS)
    coin_closes = closes_from(BTC_RETS * beta)
    vols = BASELINE[: len(coin_closes)]
    start_ms, end_ms = (END - timedelta(days=1)).timestamp() * 1000, END.timestamp() * 1000
    rows = [{"time": start_ms - 3_600_000, "rate": funding[0] / 100},
            {"time": end_ms - 3_600_000 * 9, "rate": 0.0},
            {"time": end_ms - 3_600_000, "rate": funding[1] / 100}]
    return build_attribution(
        symbol=symbol, base_asset=symbol.removesuffix("USDT"), window="24h",
        coin_metrics={"return_pct": move, "volume_quote": 100.0 + z * BASE_SD, "as_of": END.isoformat(),
                      "source": "Binance public API"},
        btc_metrics=btc_metrics if btc_metrics is not None else {"return_pct": btc_move},
        book=book if book is not None else {"depth_imbalance_pct": imbalance},
        headlines=feed if feed is not None else headlines([news[0]] * 5, [news[1]] * 5),
        raw={"coin_daily": {"candles": daily(coin_closes, vols)}, "btc_daily": {"candles": daily(btc_closes)},
             "funding": raw_funding or {"rows": rows}},
    )


# ------------------------------------------------------------------ the maths


def test_beta_on_synthetic_series_is_exact_without_noise():
    btc = daily_closes(daily(closes_from(BTC_RETS)), END)
    coin = daily_closes(daily(closes_from(0.001 + 1.5 * BTC_RETS)), END)  # a constant drift doesn't change beta
    beta, corr, n = beta_vs_benchmark(coin, btc)
    assert beta == pytest.approx(1.5, abs=1e-9) and corr == pytest.approx(1.0, abs=1e-9) and n == 30


def test_beta_with_noise_and_a_negative_beta():
    noise = np.random.default_rng(1).normal(0, 0.005, 40)
    btc = daily_closes(daily(closes_from(BTC_RETS)), END)
    beta, corr, _ = beta_vs_benchmark(daily_closes(daily(closes_from(0.8 * BTC_RETS + noise)), END), btc)
    assert beta == pytest.approx(0.8, abs=0.1) and 0.8 < corr < 1.0
    inverse, inv_corr, _ = beta_vs_benchmark(daily_closes(daily(closes_from(-0.5 * BTC_RETS)), END), btc)
    assert inverse == pytest.approx(-0.5, abs=1e-9) and inv_corr == pytest.approx(-1.0, abs=1e-9)


def test_beta_uses_finished_days_only_and_needs_enough_of_them():
    candles = daily([100.0] * 10 + [999.0])  # the last candle is today's, still open at END
    assert daily_closes(candles, END).iloc[-1] == 100.0 and len(daily_closes(candles, END)) == 10
    short = daily_closes(daily(closes_from(BTC_RETS[:12])), END)
    assert beta_vs_benchmark(short, short) == (None, None, 11)
    flat = daily_closes(daily([100.0] * 40), END)
    assert beta_vs_benchmark(flat, flat)[0] is None  # BTC didn't move at all: no variance, no beta


def test_volume_zscore_against_the_days_before_the_window():
    candles = daily([1.0] * 40, BASELINE[:40])  # 90/110 alternating, mean 100
    z, avg_w, avg_b, n = volume_zscore(300.0, 1, candles, END - timedelta(days=1))
    assert n == 30 and avg_b == 100.0 and avg_w == 300.0
    assert z == pytest.approx(200 / BASE_SD)
    z7, avg7, _, _ = volume_zscore(700.0, 7, candles, END - timedelta(days=7))  # 7 days at 100 a day
    assert avg7 == 100.0 and z7 == pytest.approx(0.0, abs=1e-9)
    assert volume_zscore(None, 1, candles, END)[0] is None  # no volume (CoinGecko backup)
    assert volume_zscore(300.0, 1, candles[-5:], END)[0] is None  # too little history


def test_news_shift_compares_the_last_24h_with_the_day_before():
    feed = headlines([-0.2] * 5, [0.1] * 5) + [{"sentiment": 0.9, "published_at": END, "redacted": True}]
    assert news_shift(feed, END) == (-0.2, 0.1, -0.3, 5, 5)  # the withheld headline doesn't count
    old = [{"sentiment": 0.5, "published_at": END - timedelta(hours=60), "redacted": False}]
    assert news_shift(headlines([0.1] * 5, [0.0] * 4) + old, END)[2] is None  # 4 earlier headlines is too few


def test_funding_rate_and_change_over_the_window():
    hour = 3_600_000
    start = END - timedelta(days=1)
    rows = [{"time": start.timestamp() * 1000 - 2 * hour, "rate": 0.0001},
            {"time": start.timestamp() * 1000 + 6 * hour, "rate": 0.0002},
            {"time": start.timestamp() * 1000 + 14 * hour, "rate": 0.0004}]
    assert funding_summary(rows, start) == (0.04, 0.03, 8)


# ------------------------------------------------------------- the thresholds


def test_defaults_cross_no_threshold_so_no_clear_driver():
    p = pack()
    assert p.drivers == [] and p.no_clear_driver and not p.small_move
    assert p.market_implied_pct == -0.2 and p.residual_pct == -2.8  # the two parts add up to the move
    assert p.market_wide.beta_vs_btc == 1.0 and p.volume.z_score == 0.0 and p.funding.funding_rate_pct == 0.01


@pytest.mark.parametrize(("factor", "at", "below"), [
    ("market_wide", {"btc_move": -1.5}, {"btc_move": -1.49}),  # market-implied part >= half of a -3% move
    ("volume", {"z": VOLUME_Z_MIN}, {"z": VOLUME_Z_MIN - 0.01}),
    ("order_book", {"imbalance": -BOOK_IMBALANCE_MIN_PCT}, {"imbalance": -BOOK_IMBALANCE_MIN_PCT + 0.01}),
    ("news", {"news": (-NEWS_SHIFT_MIN, 0.0)}, {"news": (-NEWS_SHIFT_MIN + 0.001, 0.0)}),
    ("funding", {"funding": (0.01, 0.01 + FUNDING_CHANGE_MIN_PCT)}, {"funding": (0.01, 0.039)}),
    ("funding", {"funding": (FUNDING_EXTREME_PCT, FUNDING_EXTREME_PCT)}, {"funding": (0.049, 0.049)}),
])
def test_each_factor_is_a_driver_exactly_at_its_threshold(factor, at, below):
    flagged, not_flagged = pack(**at), pack(**below)
    assert flagged.drivers == [factor] and not flagged.no_clear_driver
    assert not_flagged.drivers == [] and not_flagged.no_clear_driver


def test_directional_factors_must_point_the_way_price_moved():
    p = pack(move=-3.0, imbalance=45.0, news=(0.4, 0.0), btc_move=2.0)  # bids, better news, BTC up: price fell
    assert p.drivers == [] and p.order_book.heavier_side == "bids" and p.news.sentiment_shift == 0.4
    up = pack(move=3.0, imbalance=45.0, news=(0.4, 0.0), btc_move=2.0)
    assert up.drivers == ["market_wide", "order_book", "news"]


def test_a_small_move_is_never_attributed():
    p = pack(move=MIN_MOVE_PCT - 0.01, btc_move=0.5, z=5.0, imbalance=60.0, news=(0.5, 0.0), funding=(0.2, 0.2))
    assert p.small_move and p.drivers == [] and p.no_clear_driver
    assert pack(move=MIN_MOVE_PCT, z=5.0).drivers == ["volume"]


def test_btc_is_the_benchmark_so_its_move_is_not_split():
    p = pack(symbol="BTCUSDT", btc_metrics=None, z=3.0)
    assert not p.market_wide.available and "benchmark" in p.market_wide.note
    assert p.market_implied_pct is None and p.residual_pct is None and p.drivers == ["volume"]


def test_missing_parts_fail_soft():
    err = ToolError("timeout", "The data source timed out.")
    p = pack(btc_metrics=err, book=err, feed=err,
             raw_funding={"error": "Funding data unavailable: Binance's futures API is blocked from this "
                                   "server's region (HTTP 451)."})
    assert not any(f.available for f in (p.market_wide, p.order_book, p.news, p.funding))
    assert p.volume.available and p.no_clear_driver and "451" in p.funding.note


@respx.mock
async def test_funding_geo_block_is_reported_not_raised():
    respx.get("https://api.binance.com/api/v3/klines").mock(return_value=httpx.Response(200, json=[
        [int((TODAY - timedelta(days=i)).timestamp() * 1000), "1", "1", "1", "100", "5", 0, "500", 0, 0, 0, 0]
        for i in range(3, -1, -1)]))
    fapi = respx.get("https://fapi.binance.com/fapi/v1/fundingRate").mock(
        return_value=httpx.Response(451, json={"code": 0, "msg": "Service unavailable from a restricted location"}),
    )
    s = Settings(_env_file=None, tool_retries=0, database_url="sqlite://")
    async with httpx.AsyncClient() as http:
        raw = await fetch_raw_inputs(MarketData(s, http, SymbolRegistry()), "ETHUSDT", "24h", END)
    assert fapi.called and "HTTP 451" in raw["funding"]["error"]
    assert len(raw["coin_daily"]["candles"]) == 4 and raw["btc_daily"]["candles"]


# ------------------------------------------------------- wiring, via replay


async def test_explain_move_plan_includes_the_evidence_pack(offline_agent):
    r = await offline_agent.ask("Why is ETH down today?")
    assert [t["tool"] for t in r.trace.tools] == ["get_ticker", "compute_metrics", "attribute_move",
                                                  "get_news_sentiment"]
    assert "**Why it moved**" in r.answer and "market-implied part" in r.answer and r.trace.faithfulness == 1.0


async def test_pack_parts_follow_faults_and_poisoned_overrides(offline_agent):
    tb = offline_agent.deps.toolbox
    call = ToolCall(tool="attribute_move", args={"symbol": "BTC", "window": "24h"})
    faults = {"get_news_sentiment": "timeout", "get_order_book": "timeout"}
    down = await tb.call(call, 1, RunOptions(faults=faults))
    assert down.ok and not down.data["news"]["available"] and not down.data["order_book"]["available"]
    poison = {"get_news_sentiment": "poisoned/news_btc_ignore_rules.json"}
    poisoned = await tb.call(call, 1, RunOptions(overrides=poison))
    clean = await tb.call(call, 1)
    assert poisoned.ok and poisoned.data["news"] != clean.data["news"]
    assert "SCAMCOIN" not in str(poisoned.data).upper()
    gone = await tb.call(call, 1, RunOptions(faults={"compute_metrics": "timeout"}))
    assert not gone.ok and gone.error.code == "timeout"


# ------------------------------------------------- the "no unsupported cause" check


def test_cause_check_catches_causes_the_pack_did_not_flag():
    none: dict = {"drivers": []}
    assert unsupported_causes("ETH's drop was driven by heavy selling in the order book.", none) == ["order_book"]
    assert unsupported_causes("Headlines turned negative, which helps explain the slide.", none) == ["news"]
    assert unsupported_causes("BTC's drop weighed on ETH as well.", none) == ["market_wide"]
    assert unsupported_causes("Liquidations of leveraged longs triggered the fall.", none) == ["funding"]
    assert unsupported_causes("The fall came amid a spike in volume.", none) == ["volume"]
    assert unsupported_causes("Heavy volume points to forced selling.", {"drivers": ["volume"]}) == []


def test_cause_check_allows_facts_and_ruled_out_causes():
    none: dict = {"drivers": []}
    assert unsupported_causes("ETH fell 1.41% [1]. 24h volume: $344.92M. Asks outweigh bids.", none) == []
    assert unsupported_causes("The move doesn't look market-driven; it's coin-specific.", none) == []
    assert unsupported_causes("No clear driver found: volume and funding were unremarkable.", none) == []
    title = "Bitcoin slides as liquidations hit leveraged traders, driving funding lower"
    assert unsupported_causes(f"- {title} (CoinDesk)", none, quoted=[title]) == []
    assert unsupported_causes(f"- {title} (CoinDesk)", none) == ["funding"]


@pytest.mark.parametrize("n", range(len(DRIVERS) + 1))
def test_template_names_exactly_the_flagged_drivers(n):
    base = pack(news=(0.2, 0.0)).model_dump(mode="json")
    for subset in itertools.combinations(DRIVERS, n):
        d = {**base, "drivers": list(subset), "no_clear_driver": not subset}
        text = attribution_block(d, 1)
        assert named_causes(text) == set(subset), text
        assert ("No clear driver found" in text) == (not subset)
