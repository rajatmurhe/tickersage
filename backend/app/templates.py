"""Deterministic answers rendered straight from tool outputs.

Used for: offline mode (no LLM), the safe answer when the verifier rejects a draft
twice, the neutral data summary attached to advice refusals, and tool-failure
messages. Every number here is copied from a tool result, so these answers are
faithful by construction.
"""

from __future__ import annotations

from typing import Any

from .numbers import fmt_big, fmt_num, fmt_pct, fmt_price
from .schemas import Citation, RouteDecision, ToolResult
from .symbols import asset_name

TOOL_LABELS = {
    "get_ticker": "24h ticker",
    "get_klines": "candles",
    "get_order_book": "order book",
    "compute_metrics": "metrics",
    "compare_assets": "comparison",
    "get_news_sentiment": "news sentiment",
    "search_docs": "docs",
}


def build_citations(results: list[ToolResult]) -> tuple[list[Citation], dict[int, int], dict[str, int]]:
    """Citations plus maps from tool-result id and doc-chunk id to citation number."""
    citations: list[Citation] = []
    by_result: dict[int, int] = {}
    by_chunk: dict[str, int] = {}
    for r in results:
        if not r.ok or not r.data:
            continue
        if r.tool == "search_docs":
            for chunk in r.data.get("chunks", []):
                n = len(citations) + 1
                by_chunk[chunk["id"]] = n
                citations.append(Citation(id=n, kind="doc", title=chunk["title"], url=chunk["url"]))
            continue
        n = len(citations) + 1
        by_result[r.id] = n
        subject = r.args.get("symbol") or ", ".join(r.args.get("symbols", [])) or r.args.get("query", "")
        kind = "news" if r.tool == "get_news_sentiment" else "data"
        url = None
        if kind == "data" and (r.source or "").startswith("Binance"):
            url = "https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints"
        elif kind == "data" and (r.source or "").startswith("CoinGecko"):
            url = "https://docs.coingecko.com/reference/coins-markets"
        title = f"{r.source or 'data'}: {subject} {TOOL_LABELS[r.tool]}".strip()
        citations.append(Citation(id=n, kind=kind, title=title, url=url))
    return citations, by_result, by_chunk


def _cite(n: int | None) -> str:
    return f" [{n}]" if n else ""


def _dir(pct: float) -> str:
    return "up" if pct > 0 else "down" if pct < 0 else "flat"


def ticker_sentence(d: dict[str, Any], cite: int | None) -> str:
    base = d["base_asset"]
    pct = d["price_change_pct"]
    move = (
        f"{_dir(pct)} {fmt_pct(abs(pct), signed=False)} ({fmt_price(abs(d['price_change']))}) over the last 24 hours"
        if pct
        else "flat over the last 24 hours"
    )
    vol_base = f" ({fmt_num(d['volume_base'])} {base})" if d.get("volume_base") is not None else ""
    note = f" Note: {d['volume_note']}." if d.get("volume_note") else ""
    return (
        f"**{base}** is trading at {fmt_price(d['last_price'])}, {move}{_cite(cite)}. "
        f"24h range: {fmt_price(d['low_24h'])} to {fmt_price(d['high_24h'])}; "
        f"24h volume: {fmt_big(d['volume_quote'])}{vol_base}.{note}"
    )


def order_book_sentence(d: dict[str, Any], cite: int | None) -> str:
    side = "bids" if d["depth_imbalance_pct"] > 0 else "asks"
    return (
        f"Order book for {d['symbol']}{_cite(cite)}: best bid {fmt_price(d['best_bid'])}, best ask "
        f"{fmt_price(d['best_ask'])}, a spread of {d['spread_bps']} bps. Within 1% of the mid price there are "
        f"{fmt_big(d['bid_depth_quote_1pct'])} of bids and {fmt_big(d['ask_depth_quote_1pct'])} of asks "
        f"(depth imbalance {fmt_pct(d['depth_imbalance_pct'])}, tilted toward {side})."
    )


def metrics_sentence(d: dict[str, Any], cite: int | None, include_move: bool = True) -> str:
    w = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}[d["window"]]
    if include_move:
        parts = [
            f"Over the last {w}, **{d['base_asset']}** moved {fmt_pct(d['return_pct'])} "
            f"(from {fmt_price(d['start_price'])} to {fmt_price(d['end_price'])}){_cite(cite)}.",
            f"Range: {fmt_price(d['low'])} to {fmt_price(d['high'])}; max drawdown {fmt_pct(d['max_drawdown_pct'])}.",
        ]
    else:  # the 24h ticker already gave the move and range
        parts = [f"Hourly detail{_cite(cite)}: max drawdown {fmt_pct(d['max_drawdown_pct'])} within the window."]
    if d.get("volatility_pct") is not None:
        parts.append(
            f"Volatility: {fmt_pct(d['volatility_pct'], signed=False)} per {d['interval']} candle "
            f"({fmt_pct(d['annualized_volatility_pct'], signed=False)} annualized)."
        )
    if d.get("rsi_14") is not None:
        parts.append(
            f"RSI(14): {fmt_num(d['rsi_14'])} (readings above {d['rsi_overbought']} are conventionally called "
            f"overbought, below {d['rsi_oversold']} oversold)."
        )
    if d.get("sma_20") is not None and d.get("price_vs_sma_20_pct") is not None:
        parts.append(f"Price vs 20-period SMA ({fmt_price(d['sma_20'])}): {fmt_pct(d['price_vs_sma_20_pct'])}.")
    if d.get("vwap") is not None and d.get("price_vs_vwap_pct") is not None:
        parts.append(f"Price vs VWAP ({fmt_price(d['vwap'])}): {fmt_pct(d['price_vs_vwap_pct'])}.")
    if d.get("volume_quote") is not None:
        parts.append(f"Volume over the window: {fmt_big(d['volume_quote'])}.")
    return " ".join(parts)


def compare_block(d: dict[str, Any], cite: int | None) -> str:
    w = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}[d["window"]]
    lines = [
        f"Comparison over the last {w}{_cite(cite)}:",
        "",
        "| Asset | Price | Return | Volatility (per candle) | Annualized vol. | Max drawdown | RSI(14) | Volume |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in d["rows"]:
        vol = fmt_pct(r["volatility_pct"], signed=False) if r.get("volatility_pct") is not None else "n/a"
        ann = fmt_pct(r["annualized_volatility_pct"], signed=False) if r.get("annualized_volatility_pct") is not None else "n/a"
        rsi = fmt_num(r["rsi_14"]) if r.get("rsi_14") is not None else "n/a"
        volume = fmt_big(r["volume_quote"]) if r.get("volume_quote") is not None else "n/a"
        lines.append(
            f"| {r['base_asset']} | {fmt_price(r['end_price'])} | {fmt_pct(r['return_pct'])} | {vol} | {ann} | "
            f"{fmt_pct(r['max_drawdown_pct'])} | {rsi} | {volume} |"
        )
    rows = d["rows"]
    a, b = rows[0]["base_asset"], rows[1]["base_asset"]
    summary = [f"{d['highest_return']} had the strongest return and {d['lowest_return']} the weakest."]
    if d.get("highest_volatility"):
        summary.append(f"{d['highest_volatility']} was the most volatile.")
    if d.get("volume_ratio") is not None:
        summary.append(f"{a}'s traded volume was {d['volume_ratio']}x {b}'s.")
    lines += ["", " ".join(summary)]
    if d.get("skipped"):
        lines += ["", "Not included: " + " ".join(d["skipped"])]
    return "\n".join(lines)


def news_block(d: dict[str, Any], cite: int | None) -> str:
    if not d.get("headlines"):
        return f"I found no recent headlines for {d['query']}{_cite(cite)}."
    avg = d.get("avg_sentiment")
    tone = "mixed" if avg is None else "positive" if avg >= 0.05 else "negative" if avg <= -0.05 else "neutral"
    head = (
        f"Recent headlines{_cite(cite)} lean {tone} (average VADER score {avg}; {d['positive']} positive, "
        f"{d['negative']} negative, {d['neutral']} neutral):"
        if avg is not None
        else f"Recent headlines{_cite(cite)}:"
    )
    items = []
    for h in d["headlines"]:
        pub = f" ({h['publisher']})" if h.get("publisher") else ""
        items.append(f"- {h['title']}{pub}" + ("" if h.get("redacted") else f", sentiment {h['sentiment']}"))
    note = (
        f"\n\n{d['redacted_count']} headline(s) were withheld because they contained instructions rather than news."
        if d.get("redacted_count")
        else ""
    )
    return head + "\n" + "\n".join(items) + note


def docs_block(d: dict[str, Any], by_chunk: dict[str, int]) -> str:
    chunks = [c for c in d.get("chunks", []) if not c.get("redacted")]
    if not chunks:
        return (
            "I don't have a reference on that in my docs yet. I can explain market terms such as funding rates, "
            "spreads, RSI, VWAP or volatility, or show live data for a listed asset."
        )
    top_title = chunks[0]["title"]
    same = sorted((c for c in chunks if c["title"] == top_title), key=lambda c: int(c["id"].rsplit("#", 1)[-1]))
    text = f"**{top_title}**{_cite(by_chunk.get(same[0]['id']))}: {same[0]['text']}"
    for c in same[1:2]:
        text += f"\n\n{c['text']}{_cite(by_chunk.get(c['id']))}"
    top_score = max(c.get("score", 0.0) for c in same)
    extra = [c for c in chunks if c["title"] != top_title and c.get("score", 0.0) >= max(0.3, top_score * 0.6)][:1]
    for c in extra:
        text += f"\n\nRelated, **{c['title']}**{_cite(by_chunk.get(c['id']))}: {c['text'].split('. ')[0]}."
    return text


def error_lines(results: list[ToolResult]) -> list[str]:
    out = []
    for r in results:
        if r.ok or not r.error:
            continue
        subject = r.args.get("symbol") or ", ".join(r.args.get("symbols", [])) or r.args.get("query", "")
        what = TOOL_LABELS[r.tool]
        if r.error.code == "unknown_symbol":
            out.append(r.error.message)
        else:
            out.append(f"I couldn't get the {what} for {subject}: {r.error.message}")
    return out


def render(route: RouteDecision | None, results: list[ToolResult]) -> str:
    """Deterministic answer for any intent. Never invents a number."""
    _, by_result, by_chunk = build_citations(results)
    parts: list[str] = []
    for r in results:
        if not r.ok or not r.data:
            continue
        cite = by_result.get(r.id)
        if r.tool == "get_ticker":
            parts.append(ticker_sentence(r.data, cite))
        elif r.tool == "get_order_book":
            parts.append(order_book_sentence(r.data, cite))
        elif r.tool == "compute_metrics":
            ticker_same = any(
                o.ok and o.tool == "get_ticker" and o.args.get("symbol") == r.args.get("symbol") for o in results
            )
            parts.append(metrics_sentence(r.data, cite, include_move=not (ticker_same and r.data["window"] == "24h")))
        elif r.tool == "compare_assets":
            parts.append(compare_block(r.data, cite))
        elif r.tool == "get_news_sentiment":
            parts.append(news_block(r.data, cite))
            parts.append("Headlines show what is being discussed and its tone; they don't prove why the price moved.")
        elif r.tool == "search_docs":
            parts.append(docs_block(r.data, by_chunk))
        elif r.tool == "get_klines":
            candles = r.data.get("candles", [])
            if candles:
                last = candles[-1]
                parts.append(
                    f"Latest {r.data['interval']} candle for {r.data['symbol']}{_cite(cite)}: open "
                    f"{fmt_price(last['open'])}, high {fmt_price(last['high'])}, low {fmt_price(last['low'])}, "
                    f"close {fmt_price(last['close'])}."
                )
    errors = error_lines(results)
    if errors:
        parts.append(" ".join(errors) + (" I won't guess numbers I couldn't fetch." if any(
            r.error and r.error.code != "unknown_symbol" for r in results if not r.ok) else ""))
    if not parts:
        return "I couldn't find data to answer that."
    return "\n\n".join(parts)


def clarify_message(route: RouteDecision) -> str:
    what = {"compare": "Which assets should I compare", "explain_move": "Which asset do you mean"}.get(
        route.intent, "Which asset do you mean"
    )
    return f"{what}? For example BTC, ETH or SOL."


def advice_snapshot_intro(symbols: list[str]) -> str:
    names = ", ".join(asset_name(s) for s in symbols)
    return f"Here's a neutral snapshot of {names} instead:"
