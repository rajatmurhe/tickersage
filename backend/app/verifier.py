"""Number verifier: every figure in an answer must trace back to a tool output.

The answer's numbers are extracted (handling $, commas, %, K/M/B suffixes) and each
one must round or truncate to some number present in the tool results, including
numbers written inside tool text such as doc chunks. Numbers the user typed are
allowed too. Dates, times, window lengths ("24h", "7-day"), indicator periods
("RSI 14"), citation markers and small counting numbers are exempt.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .numbers import ExtractedNumber, extract_numbers, matches_value
from .schemas import ToolResult, VerificationReport

_SKIP_KEYS = {"open_time", "as_of", "published_at", "recorded_at", "id", "url"}


def collect_numbers(results: Iterable[ToolResult]) -> list[float]:
    values: list[float] = []
    for r in results:
        if r.ok and r.data:
            _walk(r.data, values)
    return values


def _walk(node: Any, out: list[float], key: str | None = None) -> None:
    if key in _SKIP_KEYS:
        return
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, int | float):
        out.append(float(node))
    elif isinstance(node, str):
        out.extend(n.scaled for n in extract_numbers(node, apply_exemptions=False))
    elif isinstance(node, dict):
        for k, v in node.items():
            _walk(v, out, k)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out, key)


def supported(num: ExtractedNumber, pool: list[float]) -> bool:
    return any(matches_value(num, v) for v in pool)


def verify(answer: str, results: list[ToolResult], question: str = "") -> VerificationReport:
    pool = collect_numbers(results)
    pool += [n.scaled for n in extract_numbers(question, apply_exemptions=False)]
    checked = matched = 0
    unsupported: list[str] = []
    for num in extract_numbers(answer):
        if num.is_trivial:
            continue
        checked += 1
        if supported(num, pool):
            matched += 1
        else:
            unsupported.append(num.raw)
    return VerificationReport(checked=checked, matched=matched, unsupported=unsupported)
