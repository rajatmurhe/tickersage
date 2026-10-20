"""Record the candles the research-report charts use, as replay fixtures.

    python -m evals.record_report_fixtures            # only the ones that are missing
    python -m evals.record_report_fixtures --force    # re-record them all

A report's price chart reads get_klines(symbol, interval, limit) at the candle size and count compute_metrics
uses for the window (1h x 24, 4h x 42, 1d x 30). Each one is fetched from Binance up to the time of that
coin's recorded compute_metrics snapshot, so the chart lines up with the figures in the report text.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime

from . import _BACKEND  # noqa: F401
from app.agent import Agent
from app.config import Settings
from app.tools.metrics import WINDOW_SPEC
from app.tools.registry import fixture_key

from .record_fixtures import UNIVERSE, WINDOWS
from .run_evals import EVALS_DIR


async def main(force: bool = False) -> None:
    fixtures = EVALS_DIR / "fixtures"
    settings = Settings(llm_mode="offline", tool_mode="record", database_url="sqlite://", fixtures_dir=fixtures,
                        cache_enabled=False, semantic_cache_enabled=False)
    agent = await Agent.create(settings, store_traces=False)
    tb = agent.deps.toolbox
    recorded = skipped = 0
    try:
        for base in UNIVERSE:
            pair = tb.registry.resolve(base)
            for window in WINDOWS:
                interval, limit, _ = WINDOW_SPEC[window]
                args = {"symbol": pair, "interval": interval, "limit": limit}
                path = fixtures / "tools" / f"{fixture_key('get_klines', args)}.json"
                if path.exists() and not force:
                    skipped += 1
                    continue
                metrics = fixtures / "tools" / f"{fixture_key('compute_metrics', {'symbol': pair, 'window': window})}.json"
                if not metrics.exists():
                    print(f"no compute_metrics snapshot for {pair} {window}; skipped")
                    continue
                end = datetime.fromisoformat(json.loads(metrics.read_text())["data"]["as_of"])
                out = await tb.market.get_klines(pair, interval, limit, end=end)
                tb._save("get_klines", args, "output", out.model_dump(mode="json"))
                recorded += 1
                print(f"recorded {pair} {interval} x{limit} up to {end:%Y-%m-%d %H:%M}")
    finally:
        await agent.aclose()
    print(f"done: {recorded} recorded, {skipped} already there")


if __name__ == "__main__":
    asyncio.run(main(force="--force" in sys.argv))
