"""Smart alert evals: compiling plain-language rules, clarifying questions, refusals, firing on replayed data
with a "why it moved" brief, and the scheduler's fire / re-arm / cooldown decisions.

    python -m evals.run_alert_evals                       # rules compiler, replayed data (what CI runs)
    python -m evals.run_alert_evals --gate                # exit 1 when a threshold is missed
    python -m evals.run_alert_evals --llm live            # the LLM compiler (and LLM briefs) on the same cases
    python -m evals.run_alert_evals --cases evals/cases_alerts_holdout.yaml --out ... --report ...

Results go to evals/results/alerts.json and evals/report_alerts.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from . import _BACKEND  # noqa: F401  (puts backend/ on sys.path)
from app.agent import Agent
from app.alerts.brief import write_brief
from app.alerts.compiler import AlertCompiler
from app.alerts.evaluator import check, decide
from app.alerts.spec import AlertSpec
from app.config import Settings
from app.graph import Deps
from app.guardrails import ADVICE_OUTPUT_PATTERNS
from app.llm import LLMClient, LLMUnavailable

EVALS_DIR = Path(__file__).resolve().parent
CASES = EVALS_DIR / "cases_alerts.yaml"
RESULTS = EVALS_DIR / "results" / "alerts.json"
REPORT = EVALS_DIR / "report_alerts.md"

GATES = {
    "spec_accuracy": 0.9,
    "clarify_accuracy": 0.9,
    "rejection_recall": 1.0,  # every trade, advice or injection request refused
    "false_rejections_max": 0,  # no ordinary alert refused
    "fire_accuracy": 1.0,  # replayed data: fires exactly when the recorded values meet the rule
    "cooldown_accuracy": 1.0,  # the scheduler's fire / re-arm / cooldown decisions
    "brief_faithfulness": 1.0,  # every number in every fired brief traces back to the data or the rule
    "brief_advice_max": 0,  # no advice or prediction sentence in a brief
}


def load_cases(path: Path = CASES, category: str | None = None, ids: list[str] | None = None) -> list[dict]:
    cases = yaml.safe_load(path.read_text())
    if category:
        cases = [c for c in cases if c["category"] == category]
    if ids:
        cases = [c for c in cases if c["id"] in ids]
    return cases


def _same(got: Any, want: Any) -> bool:
    if isinstance(want, float | int) and not isinstance(want, bool) and isinstance(got, float | int):
        return math.isclose(float(got), float(want), rel_tol=1e-6)
    return got == want


def spec_mismatches(spec: dict, expect: dict) -> list[str]:
    out = []
    for key, want in expect.items():
        got = spec.get(key)
        if key == "conditions":
            got = got or []
            if len(got) != len(want):
                out.append(f"conditions: {len(got)} ({[c.get('kind') for c in got]}), expected {len(want)}")
                continue
            for i, (g, w) in enumerate(zip(got, want)):
                bad = [k for k in w if not _same(g.get(k), w[k])]
                if bad:
                    out.append(f"conditions[{i}]: {', '.join(f'{k}={g.get(k)!r} (expected {w[k]!r})' for k in bad)}")
        elif not _same(got, want):
            out.append(f"{key}={got!r} (expected {want!r})")
    return out


class LlmRecorder:
    """Wraps the LLM client so the report can show each raw reply and failed provider attempts."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.last: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []

    @property
    def available(self) -> bool:
        return self.llm.available

    async def generate(self, *args: Any, **kwargs: Any):
        try:
            resp = await self.llm.generate(*args, **kwargs)
        except LLMUnavailable as exc:
            self.last = {"failed_attempts": exc.attempts}
            raise
        self.last = {"failed_attempts": resp.failed_attempts, "text": resp.text}
        self.calls.append({"tokens_in": resp.usage.input_tokens, "tokens_out": resp.usage.output_tokens})
        return resp


def run_cooldown(case: dict) -> tuple[list[str], list[str]]:
    """The scheduler's decisions over a scripted sequence of checks."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    armed, last_fired = True, None
    actions = []
    for minute, met in case["checks"]:
        now = t0 + timedelta(minutes=minute)
        action, armed = decide(met, armed, last_fired, now, case["cooldown_minutes"])
        if action == "fire":
            last_fired = now
        actions.append(action)
    fails = [] if actions == case["expect"] else [f"actions {actions}, expected {case['expect']}"]
    return actions, fails


async def run_case(case: dict, compiler: AlertCompiler, deps: Deps, brief_llm: Any) -> dict[str, Any]:
    cat = case["category"]
    out: dict[str, Any] = {"id": case["id"], "category": cat, "text": case.get("text", ""), "passed": False,
                           "failures": [], "status": None, "questions": [], "source": None}
    fails: list[str] = out["failures"]
    started = time.perf_counter()
    if cat == "cooldown":
        out["actions"], fails[:] = run_cooldown(case)
        out["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        out["passed"] = not fails
        return out

    recorder = compiler.llm if isinstance(compiler.llm, LlmRecorder) else None
    if recorder:
        recorder.last = None
    r = await compiler.compile(case["text"], case.get("answers") or {}, request_id=f"eval-{case['id']}")
    out["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    out.update(status=r.status, questions=[q.field for q in r.questions], source=r.source,
               rejection=r.rejection_category, dropped=r.dropped)
    if r.llm or r.fallback_reason:  # the model was called (screened inputs never reach it)
        out.update(llm=r.llm.model_dump() if r.llm else None, fallback_reason=r.fallback_reason)
        if recorder and recorder.last:
            out["llm_reply"] = recorder.last
    if r.spec is not None:
        out["spec"] = r.spec.model_dump(mode="json")

    if cat != "reject" and r.status == "rejected":
        fails.append(f"falsely rejected ({r.rejection_category})")
    elif cat == "compile":
        if r.status != "ok":
            fails.append(f"status {r.status}, asked about {out['questions']}")
        else:
            fails += spec_mismatches(out["spec"], case["expect"])
    elif cat == "clarify":
        if r.status != "needs_clarification":
            fails.append(f"status {r.status}, expected questions")
        elif set(out["questions"]) != set(case["expect_questions"]):
            fails.append(f"asked about {out['questions']}, expected {case['expect_questions']}")
    elif cat == "reject":
        if r.status != "rejected" or r.rejection_category != case["expect_rejection"]:
            fails.append(f"status {r.status} ({r.rejection_category}), expected {case['expect_rejection']}")
    elif cat == "fire":
        if r.spec is None:
            fails.append(f"didn't compile: {out['questions'] or r.status}")
        else:
            await fire_case(case, r.spec, r.text, deps, brief_llm, out)
    out["passed"] = not fails
    return out


async def fire_case(case: dict, spec: AlertSpec, text: str, deps: Deps, brief_llm: Any, out: dict) -> None:
    fails: list[str] = out["failures"]
    checked = await check(spec, deps.toolbox, deps.policy, request_id=f"eval-{case['id']}")
    ev = checked.evaluation
    out["met"] = ev.met
    out["observed"] = [{"condition": c.description, "met": c.met, **c.observed} for c in ev.conditions]
    if ev.errors:
        out["errors"] = ev.errors
    if ev.met is not case["expect_met"]:
        fails.append(f"met={ev.met}, expected {case['expect_met']}")
    if not ev.met:
        return
    brief = await write_brief(spec, text, checked, deps.toolbox, deps.policy, llm=brief_llm)
    advice = [s for p in ADVICE_OUTPUT_PATTERNS for s in [p.search(brief.text)] if s]
    out["brief"] = {"source": brief.source, "text": brief.text, "checked": brief.verification.checked,
                    "matched": brief.verification.matched, "unsupported": brief.verification.unsupported,
                    "fallback_reason": brief.fallback_reason, "removed": brief.removed,
                    "drivers": brief.drivers, "window": brief.window, "advice": [m.group(0) for m in advice],
                    "attribution_error": brief.attribution_error,
                    "llm": brief.llm.model_dump() if brief.llm else None}
    if brief.attribution_error:
        fails.append(f"no evidence pack: {brief.attribution_error}")
    if brief.verification.unsupported:
        fails.append(f"brief numbers not in the data: {brief.verification.unsupported}")
    if advice:
        fails.append(f"advice in the brief: {[m.group(0) for m in advice]}")


def summarize(results: list[dict]) -> dict[str, Any]:
    by_cat: dict[str, dict[str, Any]] = {}
    for r in results:
        v = by_cat.setdefault(r["category"], {"n": 0, "passed": 0})
        v["n"] += 1
        v["passed"] += int(r["passed"])
    for v in by_cat.values():
        v["pass_rate"] = round(v["passed"] / v["n"], 4)

    def rate(cat: str) -> float | None:
        return by_cat[cat]["pass_rate"] if cat in by_cat else None

    briefs = [r["brief"] for r in results if r.get("brief")]
    checked = sum(b["checked"] for b in briefs)
    matched = sum(b["matched"] for b in briefs)
    lat = sorted(r["latency_ms"] for r in results if r["category"] in ("compile", "clarify", "reject"))
    summary: dict[str, Any] = {
        "pass_rate": round(sum(r["passed"] for r in results) / len(results), 4) if results else None,
        "spec_accuracy": rate("compile"),
        "clarify_accuracy": rate("clarify"),
        "rejection_recall": rate("reject"),
        "fire_accuracy": rate("fire"),
        "cooldown_accuracy": rate("cooldown"),
        "false_rejections": sum(1 for r in results if r["category"] != "reject" and r["status"] == "rejected"),
        "briefs": len(briefs),
        "brief_sources": dict(Counter(b["source"] for b in briefs)),
        "brief_numbers_checked": checked,
        "brief_faithfulness": round(matched / checked, 4) if checked else None,
        "brief_fallbacks": sum(1 for b in briefs if b["fallback_reason"]),
        "brief_advice": sum(len(b["advice"]) for b in briefs),
        "compile_p50_ms": lat[len(lat) // 2] if lat else None,
        "by_category": by_cat,
    }
    called = [r for r in results if "fallback_reason" in r]
    if called:
        usage = [r["llm"] for r in called if r["llm"]]
        llm_lat = sorted(u["latency_ms"] for u in usage)
        fallbacks = [r["fallback_reason"] for r in called if r["fallback_reason"]]
        summary["llm"] = {
            "calls": len(called),
            "used": sum(r["source"] == "llm" for r in called),
            "rules_fallbacks": len(fallbacks),
            "fallback_reasons": dict(Counter(f.split(":")[0] for f in fallbacks)),
            "provider_fallbacks": sum(u["fell_back"] for u in usage),
            "served_by": dict(Counter(f"{u['provider']}/{u['model']}" for u in usage)),
            "cost_usd": round(sum(u["cost_usd"] for u in usage), 6),
            "latency_p50_ms": llm_lat[len(llm_lat) // 2] if llm_lat else None,
            "latency_p95_ms": llm_lat[min(len(llm_lat) - 1, math.ceil(0.95 * len(llm_lat)) - 1)] if llm_lat else None,
            "grounding_drops": sum(len(r.get("dropped") or []) for r in called),
            "cases_with_drops": sum(bool(r.get("dropped")) for r in called),
            "model_rejections": sum(1 for r in called if r["status"] == "rejected"),
        }
    return summary


def check_gates(s: dict[str, Any]) -> list[str]:
    problems = []
    for key in ("spec_accuracy", "clarify_accuracy", "rejection_recall", "fire_accuracy", "cooldown_accuracy",
                "brief_faithfulness"):
        if s[key] is not None and s[key] < GATES[key]:
            problems.append(f"{key} {s[key]} < {GATES[key]}")
    if s["false_rejections"] > GATES["false_rejections_max"]:
        problems.append(f"false rejections {s['false_rejections']} > {GATES['false_rejections_max']}")
    if s["brief_advice"] > GATES["brief_advice_max"]:
        problems.append(f"advice sentences in briefs {s['brief_advice']} > 0")
    return problems


def _p(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def render_markdown(report: dict[str, Any]) -> str:
    s, c = report["summary"], report["config"]
    lines = [
        "# TickerSage smart alerts eval report",
        "",
        f"Run `{report['run_id']}` at {report['created_at']} on `{c['cases']}`: {report['cases_run']} cases in "
        f"{report['wall_time_s']}s. Compiler: **{c['compiler']}**, data: **{c['tool_mode']}** (recorded candles, "
        "tickers and headlines).",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Pass rate | {_p(s['pass_rate'])} |",
        f"| Spec accuracy (alerts compiled to the expected spec) | {_p(s['spec_accuracy'])} |",
        f"| Clarifying questions (exactly the expected fields) | {_p(s['clarify_accuracy'])} |",
        f"| Rejection recall (trade, advice, injection) | {_p(s['rejection_recall'])} |",
        f"| False rejections | {s['false_rejections']} |",
        f"| Fire accuracy on replayed data | {_p(s['fire_accuracy'])} |",
        f"| Scheduler decisions (fire, re-arm, cooldown) | {_p(s['cooldown_accuracy'])} |",
        f"| Briefs written / numbers checked | {s['briefs']} / {s['brief_numbers_checked']} |",
        f"| Brief number faithfulness | {_p(s['brief_faithfulness'])} |",
        f"| Briefs that fell back to the template | {s['brief_fallbacks']} |",
        f"| Advice sentences in briefs | {s['brief_advice']} |",
        f"| Compile latency p50 | {s['compile_p50_ms']} ms |",
    ]
    if llm := s.get("llm"):
        served = ", ".join(f"{k} ({n})" for k, n in llm["served_by"].items()) or "none"
        reasons = ", ".join(f"{k} ({n})" for k, n in llm["fallback_reasons"].items())
        lines += [
            f"| Alerts sent to the model | {llm['calls']} |",
            f"| Compiled from the model's reply | {llm['used']} |",
            f"| Refused by the model (trade or advice) | {llm['model_rejections']} |",
            f"| Fell back to the rules parser | {llm['rules_fallbacks']}{f' ({reasons})' if reasons else ''} |",
            f"| Served by | {served} |",
            f"| Answered by a backup provider | {llm['provider_fallbacks']} |",
            f"| Numbers dropped by grounding (not in the text) | {llm['grounding_drops']} in "
            f"{llm['cases_with_drops']} case(s) |",
            f"| Model cost | ${llm['cost_usd']:.4f} |",
            f"| Model latency p50 / p95 | {llm['latency_p50_ms']} / {llm['latency_p95_ms']} ms |",
        ]
    if c.get("tokens"):
        lines.append(f"| Model tokens in / out | {c['tokens']['in']} / {c['tokens']['out']} |")
    lines += ["", "## By category", "", "| Category | Cases | Passed | Pass rate |", "|---|---|---|---|"]
    lines += [f"| {cat} | {v['n']} | {v['passed']} | {_p(v['pass_rate'])} |" for cat, v in s["by_category"].items()]
    noted = [r for r in report["cases"] if r.get("fallback_reason") or r.get("dropped")]
    if noted:
        lines += ["", "## Rules fallbacks and grounding drops", "", "| Case | Passed | What happened |", "|---|---|---|"]
        for r in noted:
            what = ([f"fell back to rules: {r['fallback_reason']}"] if r.get("fallback_reason") else []) + \
                (r.get("dropped") or [])
            lines.append(f"| {r['id']} | {'yes' if r['passed'] else 'no'} | {'; '.join(what).replace('|', '/')} |")
    fired = [r for r in report["cases"] if r["category"] == "fire"]
    if fired:
        lines += ["", "## Rules checked on the recorded data", "",
                  "| Case | Rule | Met | Measured | Brief |", "|---|---|---|---|---|"]
        for r in fired:
            measured = "; ".join(_measured(o) for o in r.get("observed", []))
            b = r.get("brief")
            brief = "" if not b else (f"{b['source']}, {b['matched']}/{b['checked']} numbers verified, window "
                                      f"{b['window']}, drivers {', '.join(b['drivers']) or 'none'}")
            lines.append(f"| {r['id']} | {r['text']} | {r.get('met')} | {measured} | {brief} |")
        example = next((r["brief"] for r in fired if r.get("brief")), None)
        if example:
            lines += ["", "Example brief (from the first fired case):", "", "> " +
                      example["text"].replace("\n", "\n> ")]
    failing = [r for r in report["cases"] if not r["passed"]]
    lines += ["", "## Failing cases", ""]
    if failing:
        lines += ["| Case | Text | Failures |", "|---|---|---|"]
        for r in failing:
            text = (r.get("text") or r["id"]).replace("|", "\\|")
            lines.append(f"| {r['id']} | {text} | {'; '.join(r['failures']).replace('|', '/')} |")
    else:
        lines.append("No failures.")
    return "\n".join(lines) + "\n"


def _measured(o: dict[str, Any]) -> str:
    skip = {"condition", "met", "interval", "period", "threshold", "window_hours", "threshold_pct", "level",
            "start_side"}
    vals = ", ".join(f"{k} {v}" for k, v in o.items() if k not in skip)
    return f"{o['condition']}: {vals}"


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings(llm_mode=args.llm, tool_mode="replay", database_url="sqlite://",
                        fixtures_dir=EVALS_DIR / "fixtures", cache_enabled=False, semantic_cache_enabled=False,
                        alerts_scheduler=False)
    cases = load_cases(args.cases, category=args.category, ids=args.ids.split(",") if args.ids else None)
    agent = await Agent.create(settings, store_traces=False)
    llm = agent.deps.llm
    recorder = LlmRecorder(llm) if llm is not None else None
    compiler = AlertCompiler(agent.deps.registry, recorder)
    started = time.perf_counter()
    try:
        results = [await run_case(case, compiler, agent.deps, recorder if not args.template_briefs else None)
                   for case in cases]
    finally:
        await agent.aclose()
    used_llm = any("fallback_reason" in r for r in results)
    tokens = None
    if recorder and recorder.calls:
        tokens = {"in": sum(c["tokens_in"] for c in recorder.calls), "out": sum(c["tokens_out"] for c in recorder.calls)}
    return {
        "run_id": uuid.uuid4().hex[:8],
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "wall_time_s": round(time.perf_counter() - started, 2),
        "cases_run": len(results),
        "config": {"compiler": "llm" if used_llm else "rules", "tool_mode": settings.tool_mode,
                   "cases": str(args.cases.relative_to(EVALS_DIR.parent)) if args.cases.is_absolute() else
                   str(args.cases), "tokens": tokens,
                   **({"primary_provider": settings.primary_provider} if used_llm else {})},
        "summary": summarize(results),
        "cases": results,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--llm", choices=["offline", "live", "auto"], default="offline")
    p.add_argument("--template-briefs", action="store_true", help="with --llm live, write briefs from the template")
    p.add_argument("--cases", type=Path, default=CASES)
    p.add_argument("--category")
    p.add_argument("--ids", help="comma-separated case ids")
    p.add_argument("--out", type=Path, default=RESULTS)
    p.add_argument("--report", type=Path, default=REPORT)
    p.add_argument("--gate", action="store_true", help="exit 1 when a CI threshold is missed")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(run(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, default=str))
    args.report.write_text(render_markdown(report))
    s = report["summary"]
    print(f"{report['cases_run']} cases · pass {_p(s['pass_rate'])} · spec {_p(s['spec_accuracy'])} · "
          f"questions {_p(s['clarify_accuracy'])} · rejections {_p(s['rejection_recall'])} · false rejections "
          f"{s['false_rejections']} · fire {_p(s['fire_accuracy'])} · cooldown {_p(s['cooldown_accuracy'])} · "
          f"briefs {s['briefs']} ({_p(s['brief_faithfulness'])} numbers verified, {s['brief_advice']} advice)")
    if llm := s.get("llm"):
        print(f"model: {llm['used']}/{llm['calls']} compiled from the reply · {llm['model_rejections']} refused · "
              f"{llm['rules_fallbacks']} rules fallbacks {llm['fallback_reasons']} · served by {llm['served_by']} · "
              f"{llm['grounding_drops']} grounding drops · ${llm['cost_usd']:.4f} · "
              f"p50 {llm['latency_p50_ms']} ms, p95 {llm['latency_p95_ms']} ms · tokens {report['config']['tokens']}")
    for r in report["cases"]:
        if not r["passed"]:
            print(f"  FAIL {r['id']}: {'; '.join(r['failures'])}")
    print(f"wrote {args.out} and {args.report}")
    if args.gate:
        problems = check_gates(s)
        if problems:
            print("gate failed: " + "; ".join(problems))
            return 1
        print("gate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
