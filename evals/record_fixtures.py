"""Record live tool responses as replay fixtures.

    python -m evals.record_fixtures            # re-record everything (wipes fixtures/tools)
    python -m evals.record_fixtures --missing  # record only calls that have no fixture yet

attribute_move re-uses the metrics, order book and news fixtures and records only its own inputs (daily
candles and funding), fetched up to the coin's metrics snapshot, so it lines up with fixtures recorded
earlier.

Runs every eval case (without injected faults or poisoned overrides) with the tools
in record mode, then records a grid of extra calls so a live LLM router or planner
that picks a different window still finds a fixture. Re-run this to refresh the
snapshot; commit the fixtures so CI is deterministic.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import shutil

from . import _BACKEND  # noqa: F401
from app.agent import Agent
from app.config import Settings
from app.schemas import ToolCall
from app.symbols import asset_name
from app.tools.registry import fixture_key

from .run_evals import EVALS_DIR, load_cases

UNIVERSE = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK", "LTC", "DOT", "TRX", "BCH"]
WINDOWS = ["24h", "7d", "30d"]


def grid_calls() -> list[ToolCall]:
    """Per-asset calls for every universe asset, so a live LLM router or planner that picks a different
    window still finds a fixture."""
    calls: list[ToolCall] = []
    for sym in UNIVERSE:
        calls.append(ToolCall(tool="get_ticker", args={"symbol": sym}))
        calls.append(ToolCall(tool="get_order_book", args={"symbol": sym, "depth": 100}))
        calls.append(ToolCall(tool="get_news_sentiment", args={"query": asset_name(sym)}))
        calls += [ToolCall(tool="compute_metrics", args={"symbol": sym, "window": w}) for w in WINDOWS]
        calls += [ToolCall(tool="attribute_move", args={"symbol": sym, "window": w}) for w in WINDOWS]
    return calls


async def record_missing(cases_path=None) -> None:
    """Replay every case and record only the tool calls that have no fixture yet."""
    fixtures = EVALS_DIR / "fixtures"
    common = dict(llm_mode="offline", database_url="sqlite://", fixtures_dir=fixtures, cache_enabled=False,
                  semantic_cache_enabled=False)
    replay = await Agent.create(Settings(tool_mode="replay", **common), store_traces=False)
    missing: dict[str, ToolCall] = {}

    def note(r) -> None:
        if r.error and r.error.code == "fixture_missing":
            args = {k: v for k, v in r.args.items() if k != "skipped"}
            missing.setdefault(fixture_key(r.tool, args), ToolCall(tool=r.tool, args=args))

    try:
        for case in load_cases(cases_path) if cases_path else load_cases():
            if case.get("faults") or case.get("overrides"):
                continue
            async for event in replay.stream(case["question"]):
                if event["type"] == "final":
                    for r in event["state"].get("results", []):
                        note(r)
        for i, call in enumerate(grid_calls()):
            note(await replay.deps.toolbox.call(call, i))
    finally:
        await replay.aclose()
    recorder = await Agent.create(Settings(tool_mode="record", **common), store_traces=False)
    try:
        for i, call in enumerate(missing.values()):
            r = await recorder.deps.toolbox.call(call, i)
            print("recorded" if r.ok else f"failed ({r.error})", call.tool, call.args)
    finally:
        await recorder.aclose()
    n = len(list((fixtures / "tools").glob("*.json")))
    meta = {"recorded_with": "evals.record_fixtures", "fixtures": n}
    (fixtures / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"done: {len(missing)} missing fixture(s), {n} tool fixtures in total")


async def main() -> None:
    fixtures = EVALS_DIR / "fixtures"
    tools_dir = fixtures / "tools"
    if tools_dir.exists():
        shutil.rmtree(tools_dir)
    settings = Settings(llm_mode="offline", tool_mode="record", database_url="sqlite://", fixtures_dir=fixtures,
                        cache_enabled=False, semantic_cache_enabled=False)
    agent = await Agent.create(settings, store_traces=False)
    tb = agent.deps.toolbox
    try:
        compare_sets: set[tuple[str, ...]] = set()
        for case in load_cases():
            if case.get("faults") or case.get("overrides"):
                continue
            resp = await agent.ask(case["question"])
            for t in resp.trace.tools:
                if t["tool"] == "compare_assets" and t["ok"]:
                    compare_sets.add(tuple(t["args"]["symbols"]))
            print(f"recorded {case['id']:<12} {[t['tool'] for t in resp.trace.tools]}")

        calls = grid_calls()
        for syms, w in itertools.product(sorted(compare_sets), WINDOWS):
            calls.append(ToolCall(tool="compare_assets", args={"symbols": list(syms), "window": w}))
        for i, call in enumerate(calls):
            r = await tb.call(call, i)
            if not r.ok:
                print("failed:", call.tool, call.args, r.error)
        n = len(list(tools_dir.glob("*.json")))
        print(f"done: {n} tool fixtures in {tools_dir}")
        meta = {"recorded_with": "evals.record_fixtures", "fixtures": n}
        (fixtures / "meta.json").write_text(json.dumps(meta, indent=1))
    finally:
        await agent.aclose()


if __name__ == "__main__":
    import sys

    from pathlib import Path

    if "--missing" in sys.argv:
        paths = [Path(a) for a in sys.argv[1:] if a.endswith(".yaml")] or [None]
        for path in paths:
            asyncio.run(record_missing(path))
    else:
        asyncio.run(main())
