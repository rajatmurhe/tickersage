"""Deterministic strategy linter: the known ways a plain-language idea turns into a broken spec.

Pure Python, no model. Each finding has a rule id, a severity and a plain explanation.
`error` findings block the backtest (the spec is impossible or doesn't match the text);
`warn` findings are shown next to the results.

Rules:
  no_stop_loss            no stop loss
  stop_wider_than_target  the stop is wider than the take profit
  size_above_cap          a position above the size cap (or above 100%, which needs leverage)
  suspicious_decimal      a number that looks like a slipped decimal (50% vs 5, 0.5 vs 5)
  rsi_out_of_range        an RSI threshold outside 0-100
  timeframe_mismatch      candles that don't match the text, holds or lookbacks shorter than one candle
  entry_never_triggers    an entry condition that can never be true
  no_exit                 nothing closes a winning trade
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel

from ..symbols import SymbolRegistry
from .parser import detect_intervals, normalize, numbers_in, parse
from .spec import (
    CANDLES, INTERVAL_HOURS, PriceChangeCondition, PriceSmaCondition, RsiCondition, SmaCrossCondition, StrategyDraft,
    bars_for_hours, describe, fmt_hours, fmt_num, lookback_bars,
)

Severity = Literal["error", "warn"]
RULES = ("no_stop_loss", "stop_wider_than_target", "size_above_cap", "suspicious_decimal", "rsi_out_of_range",
         "timeframe_mismatch", "entry_never_triggers", "no_exit")
SIZE_CAP_PCT = 25.0
WIDE_STOP_PCT = 25.0


class Finding(BaseModel):
    rule: str
    severity: Severity
    field: str
    message: str


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity == "error" for f in findings)


def lint(spec: StrategyDraft, text: str | None = None, *, partial: bool = False) -> list[Finding]:
    """Check a spec (or a draft, with `partial=True`, which skips the "something is missing" rules: the
    compiler asks about missing parts instead). `text` is the user's own wording, for the checks that
    compare the spec with what was written."""
    out: list[Finding] = []
    if not partial:
        out += _missing_exits(spec)
    out += _stop_vs_target(spec)
    out += _size(spec)
    out += _decimals(spec, text)
    out += _rsi_range(spec)
    out += _timeframes(spec, text)
    out += _never_triggers(spec)
    order = {r: i for i, r in enumerate(RULES)}
    return sorted(out, key=lambda f: (f.severity != "error", order[f.rule]))


# ------------------------------------------------------------------ rules


def _missing_exits(s: StrategyDraft) -> list[Finding]:
    out: list[Finding] = []
    others = []
    if s.take_profit_pct is not None:
        others.append("the take profit")
    if s.exit_signals:
        others.append("an exit signal")
    if s.max_hold_hours is not None:
        others.append("the time limit")
    if s.stop_loss_pct is None:
        until = " or ".join(others) if others else "the backtest ends"
        out.append(Finding(rule="no_stop_loss", severity="warn", field="stop_loss_pct", message=(
            f"There's no stop loss, so a losing trade stays open until {until} closes it. Nothing limits how much "
            "one trade can lose.")))
    if not others:
        out.append(Finding(rule="no_exit", severity="warn", field="take_profit_pct", message=(
            "Nothing closes a winning trade: there's no take profit, exit signal or time limit, so trades end only "
            "at the stop loss or when the data runs out.")))
    return out


def _stop_vs_target(s: StrategyDraft) -> list[Finding]:
    sl, tp = s.stop_loss_pct, s.take_profit_pct
    if sl is None or tp is None or sl <= tp:
        return []
    breakeven = sl / (sl + tp) * 100
    return [Finding(rule="stop_wider_than_target", severity="warn", field="stop_loss_pct", message=(
        f"The stop loss ({fmt_num(sl)}%) is wider than the take profit ({fmt_num(tp)}%): each loss is bigger than "
        f"each win, so before costs the rules must win more than {breakeven:.0f}% of trades just to break even."))]


def _size(s: StrategyDraft) -> list[Finding]:
    size = s.position_size_pct
    if size is None:
        return []
    if size > 100:
        return [Finding(rule="size_above_cap", severity="error", field="position_size_pct", message=(
            f"Each trade would use {fmt_num(size)}% of the demo balance. Anything above 100% needs leverage, which "
            "Strategy Studio doesn't model: use 100% or less."))]
    if size > SIZE_CAP_PCT:
        loss = f" With the {fmt_num(s.stop_loss_pct)}% stop, one stopped-out trade costs about " \
               f"{size * s.stop_loss_pct / 100:.2f}% of the balance before costs." if s.stop_loss_pct else ""
        return [Finding(rule="size_above_cap", severity="warn", field="position_size_pct", message=(
            f"Each trade uses {fmt_num(size)}% of the demo balance, above the {SIZE_CAP_PCT:g}% cap the linter "
            f"uses.{loss}"))]
    return []


def _is_shift(value: float, n: float) -> bool:
    return n != 0 and any(math.isclose(value, n * f, rel_tol=1e-9) for f in (10, 100, 0.1, 0.01))


def _shifted(value: float, written: list[float]) -> float | None:
    """A number in the text that `value` is 10x or 100x away from, when `value` itself isn't in the text."""
    if any(math.isclose(value, n, rel_tol=1e-9) for n in written):
        return None
    return next((n for n in written if _is_shift(value, n)), None)


def _decimals(s: StrategyDraft, text: str | None) -> list[Finding]:
    out: list[Finding] = []

    def flag(field: str, message: str, severity: Severity = "warn") -> None:
        out.append(Finding(rule="suspicious_decimal", severity=severity, field=field, message=message))

    # 1. The spec says something the text doesn't, but the text has the same digits 10x or 100x away. The
    #    rules parser's reading of the text is checked first, field by field, so a stop of 30% is caught
    #    even when 30 appears elsewhere in the text (as an RSI level, say).
    if text:
        written = numbers_in(text)
        read = parse(text, SymbolRegistry()).draft
        lowered = normalize(text).lower()
        derived = {round(n / s.demo_balance * 100, 4) for n in written}  # "$1000 per trade" -> 10%
        # (field, label, value, unit, values the text implies without writing them)
        checks: list[tuple[str, str, float | None, str, set[float]]] = [
            ("take_profit_pct", "take profit", s.take_profit_pct, "%", set()),
            ("stop_loss_pct", "stop loss", s.stop_loss_pct, "%", set()),
            ("position_size_pct", "position size", s.position_size_pct, "%", derived),
        ]
        for where, conds in (("entry", s.entry), ("exit_signals", s.exit_signals)):
            for c in conds:
                if isinstance(c, RsiCondition):
                    implied = {30.0} if "oversold" in lowered else set()
                    implied |= {70.0} if "overbought" in lowered else set()
                    checks.append((where, f"RSI({c.period}) threshold", c.value, "", implied))
                elif isinstance(c, PriceChangeCondition):
                    checks.append((where, "price move", c.pct, "%", set()))
        for field, label, value, unit, implied in checks:
            if value is None or any(math.isclose(value, a, rel_tol=1e-6) for a in implied):
                continue
            stated = getattr(read, field) if field.endswith("_pct") else None
            if stated is not None:
                n = stated if _is_shift(value, stated) else None
            else:
                n = _shifted(value, written)
            if n is not None:
                flag(field, f"The spec's {label} is {fmt_num(value)}{unit}, but your text says {fmt_num(n)}{unit}. "
                            "That looks like a slipped decimal: check which one you meant.", "error")

    # 2. Magnitudes that are almost always a typo, with or without text.
    round_trip = 2 * (s.fee_pct + s.slippage_pct)
    if s.stop_loss_pct is not None and s.stop_loss_pct >= 100:
        flag("stop_loss_pct", f"A {fmt_num(s.stop_loss_pct)}% stop loss can never fill on a long trade: the price "
                              "can't fall below zero. Did you mean a smaller number?", "error")
    elif s.stop_loss_pct is not None and s.stop_loss_pct >= WIDE_STOP_PCT:
        flag("stop_loss_pct", f"A {fmt_num(s.stop_loss_pct)}% stop loss is unusually wide for a rule-based trade. "
                              f"Check it isn't meant to be {fmt_num(s.stop_loss_pct / 10)}%.")
    for field, label, value in (("take_profit_pct", "take profit", s.take_profit_pct),
                                ("stop_loss_pct", "stop loss", s.stop_loss_pct)):
        if value is not None and value <= round_trip:
            flag(field, f"A {fmt_num(value)}% {label} is no bigger than the {fmt_num(round_trip)}% round-trip cost "
                        f"(fees plus slippage), so it's mostly noise. If you meant {fmt_num(value * 100)}%, write "
                        f"{fmt_num(value * 100)}%.")
    if s.position_size_pct is not None and s.position_size_pct < 1:
        flag("position_size_pct", f"Each trade uses only {fmt_num(s.position_size_pct)}% of the demo balance. If you "
                                  f"meant {fmt_num(s.position_size_pct * 10)}% or {fmt_num(s.position_size_pct * 100)}%"
                                  ", check the decimal.")
    for where, conds in (("entry", s.entry), ("exit_signals", s.exit_signals)):
        for c in conds:
            if isinstance(c, RsiCondition) and 0 < c.value < 1:
                flag(where, f"RSI runs from 0 to 100, so {fmt_num(c.value)} looks like {fmt_num(c.value * 100)} "
                            "written as a fraction.", "error")
    for field, label, value in (("fee_pct", "fee", s.fee_pct), ("slippage_pct", "slippage", s.slippage_pct)):
        if value >= 1:
            flag(field, f"A {fmt_num(value)}% {label} per fill is at least 10x what Binance spot charges (0.1% taker "
                        "fee). Check the decimal.")
    return out


def _rsi_range(s: StrategyDraft) -> list[Finding]:
    out = []
    for where, conds in (("entry", s.entry), ("exit_signals", s.exit_signals)):
        for c in conds:
            if isinstance(c, RsiCondition) and not 0 <= c.value <= 100:
                out.append(Finding(rule="rsi_out_of_range", severity="error", field=where, message=(
                    f"RSI always stays between 0 and 100, so '{describe(c)}' can never be true.")))
    return out


_UNIT_HOURS = {"m": 1 / 60, "h": 1.0, "d": 24.0, "w": 168.0}
_INDICATOR_UNIT = re.compile(r"\b(?P<n>\d{1,4})\s*-?\s*(?P<unit>minute|min|hour|h|day|d|week|w)s?\s*-?\s*"
                             r"(?P<ind>sma|ma|moving\s+average|simple\s+moving\s+average|rsi)\b", re.I)


def _timeframes(s: StrategyDraft, text: str | None) -> list[Finding]:
    out: list[Finding] = []
    iv = s.interval

    def flag(field: str, message: str, severity: Severity = "error") -> None:
        out.append(Finding(rule="timeframe_mismatch", severity=severity, field=field, message=message))

    if iv is None:
        return out
    ivh = INTERVAL_HOURS[iv]
    if text:
        named = detect_intervals(text)
        if named and iv not in named:
            flag("interval", f"Your text mentions {' and '.join(named)} candles, but the spec runs on {iv} candles.")
        periods = {c.period for c in s.entry + s.exit_signals if isinstance(c, (RsiCondition, PriceSmaCondition))}
        periods |= {p for c in s.entry + s.exit_signals if isinstance(c, SmaCrossCondition) for p in (c.fast, c.slow)}
        for m in _INDICATOR_UNIT.finditer(normalize(text)):
            n, unit = int(m.group("n")), m.group("unit").lower()
            unit_h = _UNIT_HOURS["m" if unit.startswith("mi") else unit[0]]
            if n in periods and not math.isclose(unit_h, ivh):
                ind = "RSI" if m.group("ind").lower() == "rsi" else "SMA"
                flag("interval", (
                    f"Your text says a {m.group(0).strip()}, but on {iv} candles {ind}({n}) covers {fmt_hours(n * ivh)}"
                    f", not {fmt_hours(n * unit_h)}. For a {n}-{m.group('unit').lower()} {ind}, run on candles of "
                    f"that length."))
    if s.max_hold_hours is not None:
        if s.max_hold_hours < ivh - 1e-9:
            flag("max_hold_hours", (
                f"A {fmt_hours(s.max_hold_hours)} maximum hold is shorter than one {iv} candle. Exits are checked once "
                f"per candle, so the shortest possible hold is one candle ({fmt_hours(ivh)})."))
        elif bars_for_hours(s.max_hold_hours, iv) >= CANDLES:
            flag("max_hold_hours", (
                f"A {fmt_hours(s.max_hold_hours)} maximum hold is {bars_for_hours(s.max_hold_hours, iv)} {iv} candles, "
                f"longer than the {CANDLES}-candle backtest, so it never fires."), "warn")
        elif not math.isclose(s.max_hold_hours / ivh, round(s.max_hold_hours / ivh)):
            n = bars_for_hours(s.max_hold_hours, iv)
            flag("max_hold_hours", (
                f"A {fmt_hours(s.max_hold_hours)} maximum hold isn't a whole number of {iv} candles; it's rounded up "
                f"to {n} candles ({fmt_hours(n * ivh)})."), "warn")
    for where, conds in (("entry", s.entry), ("exit_signals", s.exit_signals)):
        for c in conds:
            if not isinstance(c, PriceChangeCondition):
                continue
            if c.lookback_hours < ivh - 1e-9:
                flag(where, (
                    f"'{describe(c)}' measures a move over less time than one {iv} candle. Moves are measured between "
                    f"candle closes, so the shortest window is {fmt_hours(ivh)}."))
            elif not math.isclose(c.lookback_hours / ivh, round(c.lookback_hours / ivh)):
                n = bars_for_hours(c.lookback_hours, iv)
                flag(where, (
                    f"The {fmt_hours(c.lookback_hours)} window in '{describe(c)}' isn't a whole number of {iv} "
                    f"candles; it's rounded up to {n} candles."), "warn")
    return out


def _impossible(c: object, interval: str | None) -> str | None:
    """Why a single condition can never be true, or None."""
    if isinstance(c, RsiCondition):
        if c.op in ("below", "crosses_below") and c.value <= 0:
            return f"'{describe(c)}' can't happen: RSI never goes below 0."
        if c.op in ("above", "crosses_above") and c.value >= 100:
            return f"'{describe(c)}' can't happen: RSI never goes above 100."
    if isinstance(c, SmaCrossCondition) and c.fast == c.slow:
        return f"'{describe(c)}' can't happen: an average can't cross itself."
    if isinstance(c, PriceChangeCondition) and c.direction == "down" and c.pct >= 100:
        return f"'{describe(c)}' can't happen: a fall of 100% or more would take the price to zero."
    if interval is not None:
        need = lookback_bars(c, interval)  # type: ignore[arg-type]
        if need >= CANDLES:
            what = describe(c)  # type: ignore[arg-type]
            return (f"'{what}' needs {need + 1} {interval} candles of history, but the backtest loads {CANDLES}, so "
                    "it's never defined.")
    return None


def _contradiction(conds: list[object]) -> str | None:
    """Two level conditions on the same indicator that can't both hold on one candle."""
    rsi: dict[int, list[RsiCondition]] = {}
    sma: dict[int, set[str]] = {}
    for c in conds:
        if isinstance(c, RsiCondition):
            rsi.setdefault(c.period, []).append(c)
        elif isinstance(c, PriceSmaCondition):
            sma.setdefault(c.period, set()).add("above" if c.op.endswith("above") else "below")
    for period, items in rsi.items():
        ceilings = [c.value for c in items if c.op.endswith("below")]
        floors = [c.value for c in items if c.op.endswith("above")]
        if ceilings and floors and min(ceilings) <= max(floors):
            return (f"RSI({period}) can't be below {fmt_num(min(ceilings))} and above {fmt_num(max(floors))} on the "
                    "same candle.")
    for period, sides in sma.items():
        if sides == {"above", "below"}:
            return f"The close can't be both above and below SMA({period}) on the same candle."
    return None


def _never_triggers(s: StrategyDraft) -> list[Finding]:
    if not s.entry:
        return []
    reasons = [r for r in (_impossible(c, s.interval) for c in s.entry) if r]
    if s.entry_logic == "all":
        clash = _contradiction(list(s.entry))
        if clash:
            reasons.append(clash)
        blocked = bool(reasons)
    else:
        blocked = len(reasons) == len(s.entry)
    if not blocked:
        return []
    return [Finding(rule="entry_never_triggers", severity="error", field="entry",
                    message=" ".join(reasons) + " No trade would ever open.")]
