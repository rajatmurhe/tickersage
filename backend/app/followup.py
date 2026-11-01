"""Turn an elliptical follow-up ("and ETH?", "what about over 30 days?") into a full question.

The rewrite is deterministic and uses only the user's own previous question, so the result goes
through the same guardrails, router and verifier as any other question.
"""

from __future__ import annotations

import re

from .symbols import NAME_ALIASES, SymbolRegistry

MAX_HISTORY = 5
_LEAD = re.compile(r"^\s*(and|what about|how about|same for|same but|now|also|ok,? and|what of|then)\b[\s,:]*", re.I)


def is_followup(question: str) -> bool:
    words = re.findall(r"[\w$%-]+", question)
    return bool(_LEAD.match(question)) or len(words) <= 3


def _aliases(sym: str) -> list[str]:
    names = [name for name, base in NAME_ALIASES.items() if base == sym]
    return sorted({sym, *names}, key=len, reverse=True)


def resolve_followup(question: str, history: list[str], registry: SymbolRegistry) -> str:
    """Return the question to answer: `question` itself unless it only makes sense after the last turn."""
    if not history or not is_followup(question):
        return question
    prev = history[-1].strip()
    prev_syms = registry.extract(prev)
    rest = _LEAD.sub("", question).strip().rstrip("?").strip()
    new_syms = registry.extract(rest)
    if new_syms and prev_syms:
        # "and ETH?" after "What's BTC trading at?" -> "What's ETH trading at?"
        target = " and ".join(new_syms)
        out = prev
        for sym in prev_syms:
            for alias in _aliases(sym):
                out = re.sub(rf"(?<![\w$]){re.escape(alias)}(?![\w])", "\x00", out, flags=re.I)
        out = re.sub(r"\x00(\s*(,|and|vs\.?|versus)\s*\x00)*", target, out)
        return out if out != prev else question
    if not new_syms and prev_syms and rest:
        # "and over 30 days?" -> the previous question with the new detail appended
        return f"{prev.rstrip('?.! ')}, {rest}?"
    return question
