"""Backtest maths on small hand-computed series, and the no-look-ahead rules."""

import math

import numpy as np
import pandas as pd
import pytest

from app.strategy.backtest import BacktestError, candles_frame, condition_series, rsi_series, run_backtest
from app.strategy.spec import (
    PriceChangeCondition, PriceSmaCondition, RsiCondition, SmaCrossCondition, StrategySpec,
)
from app.tools.metrics import rsi


def frame(rows: list[tuple[float, float, float, float]], start: str = "2026-01-01", freq: str = "1h") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def dip_spec(**kw) -> StrategySpec:
    """Buy after a 5% one-candle drop; one candle of warm-up."""
    base = dict(symbol="ETH", interval="1h", entry=[PriceChangeCondition(direction="down", pct=5, lookback_hours=1)],
                take_profit_pct=10, stop_loss_pct=5, position_size_pct=50, fee_pct=0.1, slippage_pct=0)
    return StrategySpec(**(base | kw))


# bar: open, high, low, close
HAND = [
    (100, 101, 99, 100),
    (100, 100, 94, 94),   # -6% close to close: signal at this close
    (95, 96, 93, 95),     # entry fills at this open (95), not at the 94 signal close
    (96, 105, 95, 104),   # high 105 >= target 104.5: take profit
    (104, 104, 98, 98),   # -5.77%: signal
    (97, 97, 90, 94),     # entry at 97; low 90 <= stop 92.15: stopped out on the entry candle
    (94, 95, 93, 95),
]


def test_hand_computed_trades_and_metrics():
    r = run_backtest(dip_spec(), frame(HAND))
    t1, t2 = r.trades
    # Trade 1: 5,000 notional, 0.1% fee taken from the coins bought, sold at the 104.5 target.
    assert (t1.entry_price, t1.exit_price, t1.exit_reason, t1.bars_held) == (95, 104.5, "take_profit", 2)
    qty1 = 5000 * 0.999 / 95
    pnl1 = qty1 * 104.5 * 0.999 - 5000
    assert t1.pnl == pytest.approx(round(pnl1, 2)) == 489.01
    # Trade 2: 50% of the new equity, stopped at 97 * 0.95 = 92.15 on the entry candle.
    equity = 10000 + pnl1
    notional2 = equity * 0.5
    pnl2 = notional2 * 0.999 / 97 * 92.15 * 0.999 - notional2
    assert (t2.entry_price, t2.exit_price, t2.exit_reason, t2.bars_held) == (97, 92.15, "stop_loss", 1)
    assert t2.notional == round(notional2, 2) and t2.pnl == round(pnl2, 2)

    m = r.metrics
    final = equity + pnl2
    assert m.final_balance == round(final, 2) == 10216.82
    assert m.total_return_pct == round((final / 10000 - 1) * 100, 2) == 2.17
    assert m.trade_count == 2 and m.win_rate_pct == 50.0
    assert m.avg_trade_pct == round((pnl1 / 5000 + pnl2 / notional2) / 2 * 100, 2)
    assert m.exposure_pct == 60.0  # 3 candles held out of the 5 tradable ones (candles 2-6)
    assert m.max_drawdown_pct == round((final / equity - 1) * 100, 2) == -2.59
    # Buy-and-hold: in at the first tradable open (95), out at the last close (95), same fee each way.
    assert m.buy_and_hold_return_pct == round((0.999 * 0.999 * 95 / 95 - 1) * 100, 2) == -0.2
    proceeds2 = (pnl2 + notional2) / 0.999
    assert m.fees_paid == round((5000 + qty1 * 104.5 + notional2 + proceeds2) * 0.001, 2)
    # Equity is marked at each close from the warm-up candle on.
    assert [p.equity for p in r.equity_curve] == [10000, 9995, round(equity, 2), round(equity, 2), round(final, 2),
                                                  round(final, 2)]
    assert r.warmup_candles == 1 and r.candles == 5


def test_entry_fills_at_next_open_not_signal_close():
    r = run_backtest(dip_spec(), frame(HAND))
    signal_close = HAND[1][3]
    assert r.trades[0].entry_price == HAND[2][0] != signal_close
    assert r.trades[0].entry_time == frame(HAND).index[2]


def test_slippage_moves_both_fills_against_the_trade():
    r = run_backtest(dip_spec(slippage_pct=0.5), frame(HAND))
    t = r.trades[0]
    assert t.entry_price == pytest.approx(95 * 1.005, abs=0.005)  # prices are rounded for display
    # Levels are set from the actual fill, so the target (105.02) sits above candle 3's 105 high and the
    # stop (90.70) is hit on candle 5 instead; the exit fill is slipped too.
    assert t.exit_reason == "stop_loss"
    assert t.exit_price == round(95 * 1.005 * 0.95 * 0.995, 2)


def test_signals_never_use_future_candles():
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    rows = [(c, c * 1.01, c * 0.99, c) for c in close]
    df = frame(rows)
    conditions = [RsiCondition(op="crosses_below", value=40), RsiCondition(op="above", value=60),
                  PriceSmaCondition(period=20, op="crosses_above"),
                  SmaCrossCondition(fast=5, slow=20, op="crosses_below"),
                  PriceChangeCondition(direction="up", pct=3, lookback_hours=4)]
    for i in (60, 150, 250):
        changed = df.copy()
        changed.iloc[i + 1:] *= 3  # rewrite everything after candle i
        for cond in conditions:
            before = condition_series(cond, df, "1h").iloc[: i + 1]
            after = condition_series(cond, changed, "1h").iloc[: i + 1]
            assert before.equals(after), (cond, i)


def test_changing_the_next_open_changes_only_the_fill():
    a = run_backtest(dip_spec(take_profit_pct=50, stop_loss_pct=50), frame(HAND))
    rows = list(HAND)
    rows[2] = (90, 96, 89, 95)  # same signal candle, different next open
    b = run_backtest(dip_spec(take_profit_pct=50, stop_loss_pct=50), frame(rows))
    assert a.trades[0].entry_time == b.trades[0].entry_time
    assert (a.trades[0].entry_price, b.trades[0].entry_price) == (95, 90)


def test_stop_fills_first_when_one_candle_touches_both_levels():
    rows = HAND[:3] + [(96, 110, 85, 100), (100, 100, 100, 100)]
    r = run_backtest(dip_spec(), frame(rows))
    assert r.trades[0].exit_reason == "stop_loss" and r.trades[0].exit_price == 90.25
    assert any("stop was assumed to fill first" in n for n in r.notes)


def test_gap_through_the_stop_fills_at_the_open():
    rows = HAND[:3] + [(80, 82, 79, 81), (81, 81, 81, 81)]
    t = run_backtest(dip_spec(), frame(rows)).trades[0]
    assert (t.exit_reason, t.exit_price, t.bars_held) == ("stop_loss", 80, 1)


def test_time_exit_sells_at_the_open_after_the_last_held_candle():
    flat = [(95, 96, 94, 95)] * 6
    rows = HAND[:2] + flat
    r = run_backtest(dip_spec(take_profit_pct=None, stop_loss_pct=None, max_hold_hours=3), frame(rows))
    t = r.trades[0]
    assert (t.exit_reason, t.bars_held) == ("time_exit", 3)
    assert t.entry_time == frame(rows).index[2] and t.exit_time == frame(rows).index[5]


def test_exit_signal_fills_at_the_next_open():
    rows = HAND[:3] + [(95, 96, 94, 95), (95, 120, 95, 120), (130, 131, 129, 130), (130, 130, 130, 130)]
    spec = dip_spec(take_profit_pct=None, exit_signals=[PriceChangeCondition(direction="up", pct=10, lookback_hours=1)])
    t = run_backtest(spec, frame(rows)).trades[0]
    assert (t.exit_reason, t.exit_price) == ("exit_signal", 130)  # the +26% close fires; the next open fills


def test_open_trade_is_closed_at_the_last_close():
    rows = HAND[:3] + [(95, 96, 94, 96)]
    r = run_backtest(dip_spec(), frame(rows))
    assert r.trades[-1].exit_reason == "end_of_data" and r.trades[-1].exit_price == 96
    assert r.equity_curve[-1].equity == r.metrics.final_balance


def test_signal_on_the_last_candle_never_fills():
    r = run_backtest(dip_spec(), frame(HAND[:2] + [(95, 95, 89, 89)]))
    assert len(r.trades) == 1  # the -5.3% close on the last candle has no next open
    assert any("no next candle" in n for n in r.notes)


def test_no_trades_reports_none_not_zero():
    r = run_backtest(dip_spec(), frame([(100, 100, 100, 100)] * 5))
    m = r.metrics
    assert m.trade_count == 0 and m.win_rate_pct is None and m.avg_trade_pct is None
    assert m.total_return_pct == 0 and m.exposure_pct == 0 and m.max_drawdown_pct == 0


def test_rsi_series_matches_the_metrics_tool():
    rng = np.random.default_rng(3)
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 120))))
    series = rsi_series(close, 14)
    assert series.iloc[:14].isna().all() and not math.isnan(series.iloc[14])
    for end in (20, 60, 120):
        assert series.iloc[end - 1] == pytest.approx(rsi(close.iloc[:end]))


def test_not_enough_candles_for_the_indicators():
    spec = dip_spec(entry=[PriceSmaCondition(period=50, op="above")])
    with pytest.raises(BacktestError, match="50 candles"):
        run_backtest(spec, frame(HAND))


def test_size_above_100_is_refused():
    with pytest.raises(BacktestError):
        run_backtest(dip_spec(position_size_pct=150), frame(HAND))


def test_candles_frame_drops_a_candle_that_has_not_closed():
    data = {"candles": [{"open_time": "2026-01-01T00:00:00Z", "open": 1, "high": 1, "low": 1, "close": 1},
                        {"open_time": "2026-01-01T01:00:00Z", "open": 1, "high": 2, "low": 1, "close": 2}],
            "as_of": "2026-01-01T01:30:00Z"}
    assert len(candles_frame(data, "1h")) == 1
    data["as_of"] = "2026-01-01T02:00:00Z"
    assert len(candles_frame(data, "1h")) == 2
