"""Each linter rule fires on the case it exists for, and stays quiet on a clean spec."""

import pytest

from app.strategy.linter import RULES, has_errors, lint
from app.strategy.spec import (
    PriceChangeCondition, PriceSmaCondition, RsiCondition, SmaCrossCondition, StrategyDraft, StrategySpec,
)

TEXT = "buy ETH on 4h candles when RSI drops below 30, take profit 6%, stop loss 3%, 10% of the balance"


def spec(**kw) -> StrategySpec:
    base = dict(symbol="ETH", interval="4h", entry=[RsiCondition(op="crosses_below", value=30)], take_profit_pct=6,
                stop_loss_pct=3, position_size_pct=10)
    return StrategySpec(**(base | kw))


def rules(findings) -> dict[str, str]:
    return {f.rule: f.severity for f in findings}


def test_clean_spec_has_no_findings():
    assert lint(spec(), TEXT) == []


def test_no_stop_loss():
    f = lint(spec(stop_loss_pct=None))
    assert rules(f) == {"no_stop_loss": "warn"}
    assert "until the take profit closes it" in f[0].message


def test_no_exit_at_all():
    f = rules(lint(spec(take_profit_pct=None)))
    assert f == {"no_exit": "warn"}
    assert rules(lint(spec(take_profit_pct=None, stop_loss_pct=None))) == {"no_stop_loss": "warn", "no_exit": "warn"}


def test_missing_parts_are_not_findings_while_the_compiler_is_still_asking():
    draft = StrategyDraft(symbol="ETH", interval="4h", entry=[RsiCondition(op="below", value=30)])
    assert lint(draft, partial=True) == []


def test_stop_wider_than_target_states_the_breakeven_win_rate():
    f = lint(spec(take_profit_pct=2, stop_loss_pct=3))
    assert rules(f) == {"stop_wider_than_target": "warn"}
    assert "more than 60% of trades" in f[0].message  # 3 / (3 + 2)


def test_size_above_cap_and_above_100():
    f = lint(spec(position_size_pct=40))
    assert rules(f) == {"size_above_cap": "warn"} and "about 1.20% of the balance" in f[0].message
    assert rules(lint(spec(position_size_pct=150))) == {"size_above_cap": "error"}
    assert lint(spec(position_size_pct=25)) == []


@pytest.mark.parametrize("field,value,text,expected", [
    ("stop_loss_pct", 50, "stop loss 5%", "error"),        # spec 50%, text 5
    ("stop_loss_pct", 0.5, "stop loss 5%", "error"),       # spec 0.5%, text 5
    ("take_profit_pct", 60, "take profit 6%", "error"),
    ("position_size_pct", 1, "use 10% of the balance", "error"),
])
def test_decimal_slips_against_the_text(field, value, text, expected):
    f = lint(spec(**{field: value}), f"buy ETH on 4h candles when RSI drops below 30, {text}")
    assert f and any(x.rule == "suspicious_decimal" and x.severity == expected for x in f)
    hit = next(x for x in f if x.rule == "suspicious_decimal" and x.severity == "error")
    assert "slipped decimal" in hit.message


def test_decimal_slip_in_an_rsi_threshold():
    f = lint(spec(entry=[RsiCondition(op="below", value=3)]), "RSI below 30, take profit 6%, stop loss 3%, 4h, 10%")
    # 3 is written in the text (the stop), so only a value that isn't anywhere in it is a slip.
    assert "suspicious_decimal" not in rules(f)
    f = lint(spec(entry=[RsiCondition(op="below", value=300)]), "RSI below 30, take profit 6%, stop 2%, 4h, 10%")
    assert rules(f)["suspicious_decimal"] == "error" and rules(f)["rsi_out_of_range"] == "error"


def test_decimal_magnitudes_without_text():
    assert rules(lint(spec(stop_loss_pct=50, take_profit_pct=60)))["suspicious_decimal"] == "warn"
    assert rules(lint(spec(stop_loss_pct=100, take_profit_pct=200)))["suspicious_decimal"] == "error"
    assert rules(lint(spec(take_profit_pct=0.05)))["suspicious_decimal"] == "warn"  # below the 0.3% round trip
    assert rules(lint(spec(position_size_pct=0.5)))["suspicious_decimal"] == "warn"
    assert rules(lint(spec(entry=[RsiCondition(op="below", value=0.3)])))["suspicious_decimal"] == "error"
    assert rules(lint(spec(fee_pct=1)))["suspicious_decimal"] == "warn"


def test_oversold_and_dollar_sizes_are_not_decimal_slips():
    text = "buy ETH 4h when RSI is oversold, take profit 6%, stop 3%, $1000 per trade"
    assert lint(spec(entry=[RsiCondition(op="below", value=30)]), text) == []


def test_rsi_out_of_range_in_entry_and_exit():
    f = lint(spec(entry=[RsiCondition(op="above", value=130)]))
    assert rules(f) == {"rsi_out_of_range": "error", "entry_never_triggers": "error"}
    f = lint(spec(exit_signals=[RsiCondition(op="above", value=-5)]))
    assert rules(f) == {"rsi_out_of_range": "error"}


def test_timeframe_text_says_one_interval_spec_runs_another():
    f = lint(spec(interval="1h"), TEXT)
    assert rules(f) == {"timeframe_mismatch": "error"} and "4h" in f[0].message


def test_timeframe_day_average_on_hourly_candles():
    text = "buy BTC on 1h candles when price crosses above the 200-day moving average, tp 6% sl 3%, 10%"
    s = spec(symbol="BTC", interval="1h", entry=[PriceSmaCondition(period=200, op="crosses_above")])
    f = lint(s, text)
    assert rules(f) == {"timeframe_mismatch": "error"}
    assert "covers 200h" in f[0].message
    assert lint(s.model_copy(update={"interval": "1d"}), text.replace("1h", "1d")) == []


def test_timeframe_hold_and_lookback_shorter_than_a_candle():
    assert rules(lint(spec(max_hold_hours=2))) == {"timeframe_mismatch": "error"}
    assert rules(lint(spec(max_hold_hours=30))) == {"timeframe_mismatch": "warn"}  # rounded up to 8 candles
    assert lint(spec(max_hold_hours=24)) == []
    short = PriceChangeCondition(direction="down", pct=3, lookback_hours=1)
    assert rules(lint(spec(entry=[short]))) == {"timeframe_mismatch": "error"}


@pytest.mark.parametrize("entry,logic", [
    ([RsiCondition(op="below", value=0)], "all"),
    ([RsiCondition(op="crosses_above", value=100)], "all"),
    ([RsiCondition(op="below", value=30), RsiCondition(op="above", value=70)], "all"),
    ([PriceSmaCondition(period=50, op="above"), PriceSmaCondition(period=50, op="below")], "all"),
    ([SmaCrossCondition(fast=50, slow=50, op="crosses_above")], "all"),
    ([PriceChangeCondition(direction="down", pct=120, lookback_hours=24)], "all"),
    ([PriceSmaCondition(period=600, op="above")], "all"),  # needs more candles than the backtest loads
])
def test_entry_never_triggers(entry, logic):
    f = lint(spec(entry=entry, entry_logic=logic))
    assert rules(f).get("entry_never_triggers") == "error" and has_errors(f)
    assert "No trade would ever open" in next(x.message for x in f if x.rule == "entry_never_triggers")


def test_any_logic_needs_every_branch_impossible():
    ok = [RsiCondition(op="below", value=0), RsiCondition(op="below", value=30)]
    assert "entry_never_triggers" not in rules(lint(spec(entry=ok, entry_logic="any")))


def test_errors_sort_first_and_every_rule_is_known():
    f = lint(spec(stop_loss_pct=None, entry=[RsiCondition(op="above", value=130)]))
    assert [x.severity for x in f] == sorted((x.severity for x in f), key=lambda s: s != "error")
    assert {x.rule for x in f} <= set(RULES)
