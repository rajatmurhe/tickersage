"""Answer writing. The LLM sees only tool outputs, wrapped as untrusted data."""

from __future__ import annotations

import json
from typing import Any

from .llm import LLMClient, LLMResponse, Tier
from .prompts import FEEDBACK_TEMPLATE, SYNTH_SYSTEM
from .schemas import RouteDecision, ToolResult
from .templates import build_citations

MAX_CANDLES_IN_PROMPT = 24

# Intents the cheap model answers in routed mode; everything else goes to the strong model.
FAST_INTENTS = {"price"}


def tier_for(intent: str) -> Tier:
    return "fast" if intent in FAST_INTENTS else "strong"


def _compact(tool: str, data: dict[str, Any], by_chunk: dict[str, int]) -> dict[str, Any]:
    """Trim fields the model doesn't need, to save tokens."""
    d = dict(data)
    d.pop("as_of", None)
    d.pop("source", None)
    if tool == "get_order_book":
        d.pop("top_bids", None)
        d.pop("top_asks", None)
    elif tool == "get_klines":
        d["candles"] = [
            {k: c[k] for k in ("open_time", "open", "high", "low", "close", "volume")}
            for c in d.get("candles", [])[-MAX_CANDLES_IN_PROMPT:]
        ]
    elif tool == "get_news_sentiment":
        d["headlines"] = [
            {k: h.get(k) for k in ("title", "publisher", "sentiment", "label", "redacted")} for h in d["headlines"]
        ]
    elif tool == "search_docs":
        d["chunks"] = [
            {"cite": by_chunk.get(c["id"]), "title": c["title"], "text": c["text"], "redacted": c.get("redacted", False)}
            for c in d.get("chunks", [])
        ]
    return d


def _safe_json(obj: Any) -> str:
    # Escape angle brackets so untrusted text can't close or open tags in the prompt.
    return json.dumps(obj, ensure_ascii=False, default=str).replace("<", "\\u003c").replace(">", "\\u003e")


def build_user_prompt(question: str, route: RouteDecision, results: list[ToolResult], feedback: str | None) -> str:
    _, by_result, by_chunk = build_citations(results)
    blocks = []
    for r in results:
        attrs = f'tool="{r.tool}" trust="untrusted"'
        if r.ok and r.data is not None:
            cite = by_result.get(r.id)
            if cite:
                attrs = f'cite="{cite}" ' + attrs
            attrs += f' source="{r.source}" as_of="{r.as_of.isoformat() if r.as_of else ""}"'
            body = _safe_json(_compact(r.tool, r.data, by_chunk))
        else:
            body = _safe_json({"error": r.error.model_dump() if r.error else "unknown", "args": r.args})
            attrs += ' status="failed"'
        blocks.append(f"<tool_output {attrs}>{body}</tool_output>")
    route_line = _safe_json(route.model_dump(include={"intent", "symbols", "window"}))
    prompt = (
        f"<question>{_safe_json(question)[1:-1]}</question>\n<route>{route_line}</route>\n"
        f"<data>\n" + "\n".join(blocks) + "\n</data>"
    )
    if feedback:
        prompt += f"\n<verifier_feedback>{feedback}</verifier_feedback>"
    return prompt


async def synthesize(
    llm: LLMClient, question: str, route: RouteDecision, results: list[ToolResult], unsupported: list[str] | None
) -> LLMResponse:
    feedback = FEEDBACK_TEMPLATE.format(numbers=", ".join(unsupported)) if unsupported else None
    user = build_user_prompt(question, route, results, feedback)
    return await llm.generate(tier_for(route.intent), SYNTH_SYSTEM, user, max_tokens=8000, effort="low")
