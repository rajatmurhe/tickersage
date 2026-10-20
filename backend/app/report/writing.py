"""Writing the report's prose: per-asset sections and the editor's summary and risks.

Every LLM draft goes through the same number verifier as chat answers. A draft with a number that isn't in
the tool data is sent back once with the offending numbers listed; if the retry fails too, that part of the
report falls back to a template rendered straight from the tool outputs, which is faithful by construction.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable

from ..guardrails import check_output
from ..llm import LLMClient, LLMResponse, LLMUnavailable
from ..numbers import fmt_pct, fmt_price
from ..schemas import ToolResult
from ..symbols import asset_name
from ..synthesizer import _compact, _safe_json, normalize_citations
from ..templates import (
    TOOL_LABELS, attribution_block, metrics_sentence, news_block, ticker_sentence,
)
from ..verifier import verify
from .models import SectionCheck
from .prompts import FEEDBACK, SECTION_SYSTEM, WRITER_SCHEMA, WRITER_SYSTEM

MAX_ATTEMPTS = 2  # one draft and one retry with the verifier's feedback, as in chat
WINDOW_WORDS = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}
HEADLINE_CAVEAT = "Headlines show what is being discussed and its tone; they don't prove why the price moved."
_CITE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def first(results: list[ToolResult], tool: str) -> ToolResult | None:
    return next((r for r in results if r.ok and r.data and r.tool == tool), None)


def renumber(text: str, local: dict[int, int], global_: dict[int, int]) -> str:
    """Rewrite citation markers from a sub-agent's own numbering (local cite -> result id) to the report's."""
    def sub(m: re.Match[str]) -> str:
        nums = []
        for part in m.group(1).split(","):
            rid = local.get(int(part))
            n = global_.get(rid) if rid is not None else None
            if n is not None and n not in nums:
                nums.append(n)
        return "[" + ", ".join(map(str, nums)) + "]" if nums else ""
    return _CITE.sub(sub, text)


def data_blocks(results: list[ToolResult], cites: dict[int, int], *, skip: tuple[str, ...] = ("get_klines",)) -> str:
    """Tool outputs as <tool_output> blocks for a prompt, failed and denied calls included."""
    blocks = []
    for r in results:
        if r.tool in skip:
            continue
        attrs = f'tool="{r.tool}" trust="untrusted"'
        if r.ok and r.data is not None:
            if cites.get(r.id):
                attrs = f'cite="{cites[r.id]}" ' + attrs
            data = _compact(r.tool, r.data, {}) if "headlines" in r.data or r.tool != "get_news_sentiment" \
                else dict(r.data)
            if r.tool == "attribute_move":
                data.pop("thresholds", None)
            body = _safe_json(data)
        else:
            body = _safe_json({"error": r.error.model_dump() if r.error else "unknown", "args": r.args})
            attrs += ' status="failed"'
        blocks.append(f"<tool_output {attrs}>{body}</tool_output>")
    return "\n".join(blocks)


# ------------------------------------------------------------------ templates


def section_template(results: list[ToolResult], cites: dict[int, int], missing: list[str]) -> str:
    """One asset's section from its tool outputs. `missing` names data the policy engine didn't allow."""
    parts: list[str] = []
    ticker, metrics = first(results, "get_ticker"), first(results, "compute_metrics")
    attribution, news = first(results, "attribute_move"), first(results, "get_news_sentiment")
    if ticker:
        parts.append(ticker_sentence(ticker.data, cites.get(ticker.id)))  # type: ignore[arg-type]
    if metrics:
        parts.append(metrics_sentence(metrics.data, cites.get(metrics.id)))  # type: ignore[arg-type]
    if attribution:
        parts.append(attribution_block(attribution.data, cites.get(attribution.id)))  # type: ignore[arg-type]
    if news:
        parts.append(news_block(news.data, cites.get(news.id)) + "\n\n" + HEADLINE_CAVEAT)  # type: ignore[arg-type]
    gaps = []
    for r in results:
        if not r.ok and r.error:
            what = TOOL_LABELS[r.tool]
            gaps.append(r.error.message if r.error.code == "unknown_symbol" else
                        f"The {what} couldn't be fetched: {r.error.message}")
    gaps += [f"The {m} wasn't fetched: this request's permissions don't allow it." for m in missing]
    if gaps:
        parts.append(" ".join(gaps) + " Nothing here is guessed in their place.")
    return "\n\n".join(parts) if parts else "No data could be fetched for this asset."


def summary_template(assets: list[str], window: str, by_asset: dict[str, list[ToolResult]],
                     compare: ToolResult | None, cites: dict[int, int]) -> str:
    w = WINDOW_WORDS[window]
    moves = []
    for a in assets:
        m = first(by_asset.get(a, []), "compute_metrics")
        if m:
            d = m.data or {}
            moves.append(f"{a} moved {fmt_pct(d['return_pct'])} (from {fmt_price(d['start_price'])} to "
                         f"{fmt_price(d['end_price'])}){_cite(cites.get(m.id))}")
        else:
            moves.append(f"{a}'s return couldn't be fetched")
    text = f"This report covers {_join(assets)} over the last {w}. " + _sentence(moves)
    if compare and compare.data:
        d = compare.data
        text += (f" Across the set, {d['highest_return']} had the strongest return and {d['lowest_return']} the "
                 f"weakest{_cite(cites.get(compare.id))}.")
        if d.get("highest_volatility"):
            text += f" {d['highest_volatility']} was the most volatile."
    return text


def risks_template(assets: list[str], by_asset: dict[str, list[ToolResult]], cites: dict[int, int]) -> list[str]:
    out = []
    for a in assets:
        m = first(by_asset.get(a, []), "compute_metrics")
        if not m or not m.data:
            continue
        d = m.data
        line = f"{a} had a max drawdown of {fmt_pct(d['max_drawdown_pct'])} inside the window"
        if d.get("annualized_volatility_pct") is not None:
            line += f" and annualized volatility of {fmt_pct(d['annualized_volatility_pct'], signed=False)}"
        out.append(line + f"{_cite(cites.get(m.id))}.")
    return out


def standing_caveats(gaps: list[str], has_news: bool) -> list[str]:
    """Caveats every report carries, outside the verified prose: data gaps first, then the fixed ones."""
    out = list(gaps)
    if has_news:
        out.append(HEADLINE_CAVEAT)
    out.append("Past moves and volatility describe the window shown; they don't predict future prices.")
    out.append("This is market research from public data, not investment advice.")
    return out


def _cite(n: int | None) -> str:
    return f" [{n}]" if n else ""


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _sentence(parts: list[str]) -> str:
    if not parts:
        return ""
    return "Over the window, " + "; ".join(parts) + "."


# ------------------------------------------------------------------- LLM parts


async def _verified(
    draft: Callable[[list[str] | None], Awaitable[LLMResponse]],
    parse: Callable[[str], str],
    results: list[ToolResult],
    question: str,
) -> tuple[str | None, SectionCheck, list[LLMResponse], str | None]:
    """Draft, verify, retry once with the unsupported numbers. Returns (text or None, check, responses, error)."""
    responses: list[LLMResponse] = []
    unsupported: list[str] | None = None
    check = SectionCheck(mode="llm")
    error: str | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = await draft(unsupported)
        except LLMUnavailable as exc:
            check.mode, check.fell_back = "template", True
            return None, check, responses, f"llm unavailable: {exc}"
        responses.append(resp)
        check.attempts = attempt
        try:
            text = parse(resp.text)
        except (ValueError, KeyError, TypeError) as exc:  # unparseable output counts as a failed attempt
            error, unsupported = f"unusable output: {exc}", None
            continue
        report = verify(text, results, question)
        check.checked, check.matched, check.unsupported = report.checked, report.matched, report.unsupported
        if not report.unsupported:
            return text, check, responses, None
        error, unsupported = None, report.unsupported
    check.mode, check.fell_back = "template", True
    return None, check, responses, error


async def write_section(llm: LLMClient, question: str, asset: str, window: str, results: list[ToolResult],
                        cites: dict[int, int], missing: list[str]):
    """The sub-agent's section, verified against its own tool outputs only."""
    head = (f"<request>{_safe_json(question)[1:-1]}</request>\n<asset>{asset} ({asset_name(asset)})</asset>\n"
            f"<window>last {WINDOW_WORDS[window]}</window>\n")
    if missing:
        head += f"<not_fetched>{_safe_json(missing)}</not_fetched>\n"
    body = f"<data>\n{data_blocks(results, cites)}\n</data>"

    async def draft(unsupported: list[str] | None) -> LLMResponse:
        user = head + body + (f"\n<verifier_feedback>{FEEDBACK.format(numbers=', '.join(unsupported))}"
                              f"</verifier_feedback>" if unsupported else "")
        return await llm.generate("fast", SECTION_SYSTEM, user, max_tokens=6000, effort="low")

    return await _verified(draft, lambda text: normalize_citations(text.strip()), results, question)


async def write_summary(llm: LLMClient, question: str, assets: list[str], window: str, results: list[ToolResult],
                        cites: dict[int, int]):
    """The editor's summary and risks, verified against every tool output in the report."""
    head = (f"<request>{_safe_json(question)[1:-1]}</request>\n<assets>{', '.join(assets)}</assets>\n"
            f"<window>last {WINDOW_WORDS[window]}</window>\n")
    # The editor needs the headline figures, not every headline: news is cut to its tone.
    slim = [_slim(r) for r in results]
    body = f"<data>\n{data_blocks(slim, cites)}\n</data>"

    async def draft(unsupported: list[str] | None) -> LLMResponse:
        user = head + body + (f"\n<verifier_feedback>{FEEDBACK.format(numbers=', '.join(unsupported))}"
                              f"</verifier_feedback>" if unsupported else "")
        return await llm.generate("strong", WRITER_SYSTEM, user, json_schema=WRITER_SCHEMA, max_tokens=8000,
                                  effort="low")

    return await _verified(draft, lambda text: join_writer(*parse_writer(text)), results, question)


def parse_writer(text: str) -> tuple[str, list[str]]:
    data = json.loads(_strip_fence(text))
    summary = normalize_citations(str(data["summary"]).strip())
    risks = [normalize_citations(str(r).strip().lstrip("-•* ").strip()) for r in data["risks"] if str(r).strip()]
    if not summary:
        raise ValueError("empty summary")
    return summary, risks


def join_writer(summary: str, risks: list[str]) -> str:
    """Summary and risks as one text, so the verifier checks both; split_writer undoes it."""
    return summary + "\n\n" + "\n".join(f"- {r}" for r in risks)


def split_writer(text: str) -> tuple[str, list[str]]:
    summary, _, rest = text.partition("\n\n")
    return summary.strip(), [line[2:].strip() for line in rest.splitlines() if line.startswith("- ")]


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t


def _slim(r: ToolResult) -> ToolResult:
    if r.ok and r.data and r.tool == "get_news_sentiment":
        keep = ("query", "headline_count", "avg_sentiment", "positive", "negative", "neutral", "redacted_count")
        return r.model_copy(update={"data": {k: r.data.get(k) for k in keep}})
    return r


def guard(text: str) -> tuple[str, list[str]]:
    """The output guard over one written part: drops sentences that read like advice or predictions."""
    check = check_output(text)
    return check.text, check.removed


def facts_for_judge(results: list[ToolResult]) -> list[ToolResult]:
    """Tool outputs the judge sees: candles are left out (they only feed the chart)."""
    return [_slim(r) for r in results if r.tool != "get_klines"]


__all__ = [
    "MAX_ATTEMPTS", "WINDOW_WORDS", "data_blocks", "facts_for_judge", "first", "guard", "join_writer",
    "parse_writer", "renumber", "risks_template", "section_template", "split_writer", "standing_caveats",
    "summary_template", "write_section", "write_summary",
]

