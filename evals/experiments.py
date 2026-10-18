"""The four README experiments.

    python -m evals.experiments                 # runs what the available keys allow
    python -m evals.experiments --only cache

1. routed vs single model      cost, latency, correctness        needs ANTHROPIC_API_KEY
2. verifier on vs off          number faithfulness               needs an LLM key (offline answers are
                                                                 templated, so the verifier has nothing to catch)
3. cache on vs off             latency on live tools             runs anywhere with network access
4. Claude vs OpenAI primary    the same eval on both providers   needs both keys

Writes evals/experiments.md.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from datetime import UTC, datetime
from typing import Any

from . import _BACKEND  # noqa: F401
from app.agent import Agent
from app.config import Settings

from .run_evals import EVALS_DIR, load_cases, parse_args, run

OUT = EVALS_DIR / "experiments.md"
CACHE_CATEGORIES = {"price_lookup", "comparison", "explain_move"}


def _row(name: str, report: dict[str, Any]) -> str:
    s = report["summary"]

    def p(x: float | None) -> str:
        return "n/a" if x is None else f"{x * 100:.1f}%"

    return (
        f"| {name} | {p(s['pass_rate'])} | {p(s['number_faithfulness'])} | {p(s['judge_score'])} | "
        f"{s['latency_p50_ms']} | {s['latency_p95_ms']} | ${s['avg_cost_usd']:.5f} | {', '.join(report['config']['models'])} |"
    )


HEADER = [
    "| Config | Pass rate | Faithfulness | Judge | p50 ms | p95 ms | Cost / query | Models |",
    "|---|---|---|---|---|---|---|---|",
]


async def eval_config(extra: list[str]) -> dict[str, Any]:
    return await run(parse_args(["--llm", "live", "--judge", *extra]))


async def exp_routing() -> list[str]:
    routed = await eval_config(["--routing", "routed"])
    single = await eval_config(["--routing", "single"])
    return ["## 1. Routed vs single strong model", "", *HEADER, _row("routed (Haiku + Sonnet)", routed),
            _row("single (Sonnet everywhere)", single)]


async def exp_verifier() -> list[str]:
    on = await eval_config([])
    off = await eval_config(["--no-verifier"])
    return ["## 2. Number verifier on vs off", "", *HEADER, _row("verifier on", on), _row("verifier off", off)]


async def exp_providers() -> list[str]:
    claude = await eval_config(["--provider", "anthropic"])
    openai = await eval_config(["--provider", "openai"])
    return ["## 4. Claude vs OpenAI as the primary model", "", *HEADER, _row("Claude primary", claude),
            _row("OpenAI primary", openai)]


async def _timed_pass(agent: Agent, questions: list[str]) -> list[float]:
    out = []
    for q in questions:
        t = time.perf_counter()
        await agent.ask(q)
        out.append((time.perf_counter() - t) * 1000)
    return out


async def exp_cache() -> list[str]:
    questions = [c["question"] for c in load_cases() if c["category"] in CACHE_CATEGORIES
                 and not c.get("faults") and not c.get("overrides")][:24]
    rows = []
    for enabled in (True, False):
        s = Settings(llm_mode="offline", tool_mode="live", cache_enabled=enabled, semantic_cache_enabled=enabled,
                     database_url="sqlite://")
        agent = await Agent.create(s, store_traces=False)
        try:
            await _timed_pass(agent, questions)  # first pass warms the cache (or not)
            second = sorted(await _timed_pass(agent, questions))
        finally:
            await agent.aclose()
        rows.append((enabled, statistics.median(second), second[int(0.95 * (len(second) - 1))], statistics.fmean(second)))
    lines = ["## 3. Market-data cache on vs off", "",
             f"{len(questions)} price, comparison and explain-move questions against live public APIs, asked twice "
             "in a row. Timings are for the second pass (LLM offline, so this isolates tool latency).", "",
             "| Cache | p50 ms | p95 ms | mean ms |", "|---|---|---|---|"]
    for enabled, p50, p95, mean in rows:
        lines.append(f"| {'on' if enabled else 'off'} | {p50:.1f} | {p95:.1f} | {mean:.1f} |")
    on, off = rows[0], rows[1]
    if on[1] > 0:
        lines += ["", f"Median speed-up with the cache warm: {off[1] / on[1]:.0f}x."]
    return lines


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["routing", "verifier", "cache", "providers"])
    args = ap.parse_args()
    s = Settings()
    has_claude, has_openai = bool(s.anthropic_api_key), bool(s.openai_api_key)
    sections: list[list[str]] = []
    plan = [
        ("routing", exp_routing, has_claude, "needs ANTHROPIC_API_KEY"),
        ("verifier", exp_verifier, has_claude or has_openai, "needs an LLM API key"),
        ("cache", exp_cache, True, ""),
        ("providers", exp_providers, has_claude and has_openai, "needs ANTHROPIC_API_KEY and OPENAI_API_KEY"),
    ]
    for name, fn, ok, why in plan:
        if args.only and name != args.only:
            continue
        if not ok:
            sections.append([f"## {name}", "", f"Skipped: {why}."])
            print(f"skipped {name}: {why}")
            continue
        print(f"running {name} …")
        sections.append(await fn())
    body = [f"# TickerSage experiments", "", f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.", ""]
    for sec in sections:
        body += sec + [""]
    OUT.write_text("\n".join(body))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
