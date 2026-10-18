"""LLM-as-judge with a fixed rubric. Needs a live provider; spot-check its verdicts by hand."""

from __future__ import annotations

import json
from typing import Any

from app.llm import LLMClient, LLMUnavailable
from app.prompts import JUDGE_SCHEMA, JUDGE_SYSTEM
from app.schemas import ToolResult
from app.synthesizer import _compact, _safe_json
from app.templates import build_citations

CRITERIA = ["answers_question", "grounded", "no_advice", "handles_gaps", "clear"]


async def judge(llm: LLMClient, question: str, answer: str, results: list[ToolResult]) -> dict[str, Any]:
    _, _, by_chunk = build_citations(results)
    data = [
        {"tool": r.tool, "ok": r.ok, "data": _compact(r.tool, r.data, by_chunk) if r.ok and r.data else None,
         "error": r.error.model_dump() if r.error else None}
        for r in results
    ]
    user = (
        f"<question>{_safe_json(question)}</question>\n<answer>{_safe_json(answer)}</answer>\n"
        f"<tool_data>{_safe_json(data)}</tool_data>"
    )
    try:
        resp = await llm.generate("strong", JUDGE_SYSTEM, user, json_schema=JUDGE_SCHEMA, max_tokens=4000,
                                  effort="medium")
    except LLMUnavailable as exc:
        return {"error": str(exc)}
    verdict = json.loads(resp.text)
    verdict["score"] = sum(verdict[c] for c in CRITERIA) / len(CRITERIA)
    verdict["cost_usd"] = resp.cost_usd
    return verdict
