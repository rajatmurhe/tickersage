"""The four README experiments.

    python -m evals.experiments                 # runs what the available keys allow
    python -m evals.experiments --only cache

1. routed vs single model      cost, latency, correctness        needs an LLM key with a fast/strong pair
2. verifier on vs off          number faithfulness               needs an LLM key (offline answers are
                                                                 templated, so the verifier has nothing to catch)
3. cache on vs off             latency on live tools             runs anywhere with network access
4. Claude vs OpenAI primary    the same eval on both providers   needs ANTHROPIC_API_KEY + OPENAI_API_KEY,
                                                                 or OPENROUTER_API_KEY (both families through it)

Experiments 1 and 2 run on the primary provider (PRIMARY_PROVIDER, or the first one with a key). In
experiment 4, Claude runs on the Claude API when ANTHROPIC_API_KEY is set, otherwise through OpenRouter.

Writes evals/experiments.md.
"""

from __future__ import annotations

import argparse
import json
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
# GPT models with the same fast/strong split as Haiku/Sonnet, for experiment 4 through OpenRouter.
OPENROUTER_GPT = "openai/gpt-5-mini,openai/gpt-5"


def live_provider(s: Settings) -> str | None:
    """The configured primary provider if it has a key, otherwise the first provider that has one."""
    keys = {"anthropic": s.anthropic_api_key, "openai": s.openai_api_key, "openrouter": s.openrouter_api_key,
            "nvidia": s.nvidia_api_key, "groq": s.groq_api_key}
    if keys.get(s.primary_provider):
        return s.primary_provider
    return next((name for name, key in keys.items() if key), None)


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


# Extra run_evals flags shared by every experiment run (cases file, concurrency, judge provider, ...).
SHARED: list[str] = []
RESULTS_DIR = EVALS_DIR / "results"


async def eval_config(extra: list[str], save_as: str | None = None) -> dict[str, Any]:
    report = await run(parse_args(["--llm", "live", "--judge", *SHARED, *extra]))
    if save_as:  # keep every case, so the README's failure analysis can cite real answers
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / f"exp_{save_as}.json").write_text(json.dumps(report, indent=1, default=str))
    return report


def _setup(report: dict[str, Any]) -> str:
    c = report["config"]
    judges = ", ".join(c.get("judge_models") or []) or "same client"
    return (f"{report['cases_run']} cases, provider `{c['primary_provider']}`, provider fallback "
            f"{'on' if c.get('provider_fallback', True) else 'off'}, judge: {judges}. Run {report['created_at']}.")


async def exp_routing(provider: str) -> list[str]:
    routed = await eval_config(["--provider", provider, "--routing", "routed"], "routing_routed")
    single = await eval_config(["--provider", provider, "--routing", "single"], "routing_single")
    return ["## 1. Routed vs single strong model", "", _setup(routed), "", *HEADER,
            _row("routed (fast + strong model)", routed), _row("single (strong model everywhere)", single)]


async def exp_verifier(provider: str) -> list[str]:
    on = await eval_config(["--provider", provider], "verifier_on")
    off = await eval_config(["--provider", provider, "--no-verifier"], "verifier_off")
    return ["## 2. Number verifier on vs off", "", _setup(on), "", *HEADER, _row("verifier on", on),
            _row("verifier off", off)]


async def exp_providers(provider: str) -> list[str]:
    if provider == "openrouter":  # one key, both model families
        claude = await eval_config(["--provider", "openrouter"])
        gpt = await eval_config(["--provider", "openrouter", "--models", OPENROUTER_GPT])
        labels = ("Claude primary (via OpenRouter)", "OpenAI primary (via OpenRouter)")
    else:
        claude = await eval_config(["--provider", "anthropic"])
        gpt = await eval_config(["--provider", "openai"])
        labels = ("Claude primary", "OpenAI primary")
    return ["## 4. Claude vs OpenAI as the primary model", "", *HEADER, _row(labels[0], claude),
            _row(labels[1], gpt)]


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


NAMES = ["routing", "verifier", "cache", "providers"]


def _previous_sections() -> dict[str, list[str]]:
    """Sections of the existing file by experiment name, so `--only` updates one without losing the others."""
    if not OUT.exists():
        return {}
    out: dict[str, list[str]] = {}
    name = None
    for line in OUT.read_text().splitlines():
        if line.startswith("<!-- exp:"):
            name = line.removeprefix("<!-- exp:").removesuffix(" -->").strip()
            out[name] = []
        elif line.startswith("## "):
            h = line.lower()  # files written before the markers existed
            guess = next((n for n, keys in (("routing", ("routing", "routed")), ("verifier", ("verifier",)),
                                            ("cache", ("cache",)), ("providers", ("provider", "primary model")))
                          if any(k in h for k in keys)), None)
            if name is None or guess not in out:
                name = guess or name
                out.setdefault(name, [])
            out[name].append(line)
        elif name is not None:
            out[name].append(line)
    return {k: _trim(v) for k, v in out.items() if k}


def _trim(lines: list[str]) -> list[str]:
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", choices=NAMES, help="run these; the other sections of the file are kept")
    ap.add_argument("--provider", help="provider for experiments 1 and 2 (default: the primary provider)")
    ap.add_argument("--cases", help="cases file for the live experiments (default: evals/cases.yaml)")
    ap.add_argument("--concurrency", type=int)
    ap.add_argument("--judge-provider")
    ap.add_argument("--no-provider-fallback", action="store_true")
    args = ap.parse_args()
    for flag, value in (("--cases", args.cases), ("--concurrency", args.concurrency),
                        ("--judge-provider", args.judge_provider)):
        if value is not None:
            SHARED.extend([flag, str(value)])
    if args.no_provider_fallback:
        SHARED.append("--no-provider-fallback")
    s = Settings()
    live = args.provider or live_provider(s)
    both = "anthropic" if s.anthropic_api_key and s.openai_api_key else "openrouter" if s.openrouter_api_key else None
    sections: dict[str, list[str]] = {}
    plan = [
        ("routing", lambda: exp_routing(live), live, "needs an LLM API key"),
        ("verifier", lambda: exp_verifier(live), live, "needs an LLM API key"),
        ("cache", exp_cache, True, ""),
        ("providers", lambda: exp_providers(both), both,
         "needs ANTHROPIC_API_KEY and OPENAI_API_KEY, or OPENROUTER_API_KEY"),
    ]
    previous = _previous_sections()
    for name, fn, ok, why in plan:
        if args.only and name not in args.only:
            if name in previous:
                sections[name] = previous[name]
            continue
        if not ok:
            sections[name] = [f"## {name}", "", f"Skipped: {why}."]
            print(f"skipped {name}: {why}")
            continue
        print(f"running {name} …")
        sections[name] = await fn()
    body = ["# TickerSage experiments", "", f"Updated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.", ""]
    for name in NAMES:
        if name in sections:
            body += [f"<!-- exp:{name} -->", *sections[name], ""]
    OUT.write_text("\n".join(body))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
