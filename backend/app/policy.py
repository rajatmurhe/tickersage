"""Deterministic permission layer between the planner and the tools.

The planner (rules or LLM) only proposes tool calls; this module decides which of them run.
Each call is checked against the request's capability scopes, an allow-list of tool names,
argument limits and a per-request call budget. It is plain Python with no model in the loop,
so the same plan always gets the same decisions and no prompt can argue its way past them.

It mirrors how exchanges let AI agents act only within the permissions a user set on an API
key. Every scope here is read-only, and there is no scope (and no tool) that can trade.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any

from .schemas import ToolCall
from .templates import TOOL_LABELS
from .tools.registry import TOOL_EXTRA_SCOPES, TOOL_SCOPES

if TYPE_CHECKING:
    from .config import Settings

DEFAULT_SCOPES = frozenset({"market:read", "news:read", "docs:read"})
WINDOWS = ("24h", "7d", "30d")
INTERVALS = ("15m", "1h", "4h", "1d")


@dataclass(frozen=True)
class Decision:
    tool: str
    args: dict[str, Any]
    scope: str | None  # the scope the tool needs; None for a tool that doesn't exist
    decision: str  # "allow" | "deny"
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Policy:
    scopes: frozenset[str] = DEFAULT_SCOPES
    allowed_tools: frozenset[str] = frozenset(TOOL_SCOPES)
    max_calls: int = 6  # tool calls per request
    max_symbols: int = 5  # symbols per call
    windows: tuple[str, ...] = WINDOWS
    intervals: tuple[str, ...] = INTERVALS
    max_depth: int = 100  # order-book levels
    max_candles: int = 500

    @classmethod
    def from_settings(cls, s: Settings) -> Policy:
        return cls(
            scopes=frozenset(s.policy_scopes),
            allowed_tools=frozenset(s.policy_allowed_tools or TOOL_SCOPES),
            max_calls=s.policy_max_tool_calls,
            max_symbols=s.policy_max_symbols,
            max_depth=s.policy_max_order_book_depth,
        )

    def for_request(self, scopes: Iterable[str] | None) -> Policy:
        """This policy narrowed to the scopes a request carries. A request can drop scopes, never add them."""
        return self if scopes is None else replace(self, scopes=self.scopes & frozenset(scopes))

    def allows_tool(self, tool: str) -> bool:
        """Whether this request could call `tool` at all (allow-list and scope, ignoring arguments)."""
        needs = (TOOL_SCOPES.get(tool), *TOOL_EXTRA_SCOPES.get(tool, ()))
        return tool in self.allowed_tools and all(scope in self.scopes for scope in needs)

    def review(self, calls: list[ToolCall], already_allowed: int = 0) -> list[Decision]:
        """One decision per proposed call, in plan order. Calls past the per-request budget are denied."""
        decisions = []
        used = already_allowed
        for call in calls:
            d = self.check(call.tool, dict(call.args), used)
            used += d.allowed
            decisions.append(d)
        return decisions

    def check(self, tool: str, args: dict[str, Any], already_allowed: int = 0) -> Decision:
        scope = TOOL_SCOPES.get(tool)

        def deny(reason: str) -> Decision:
            return Decision(tool, args, scope, "deny", reason)

        if scope is None:
            return deny("not on the tool allow-list: there is no such read-only tool")
        if tool not in self.allowed_tools:
            return deny("not on the tool allow-list for this deployment")
        if scope not in self.scopes:
            return deny(f"needs the {scope} scope, which this request doesn't have")
        for extra in TOOL_EXTRA_SCOPES.get(tool, ()):
            if extra not in self.scopes:
                return deny(f"also returns data that needs the {extra} scope, which this request doesn't have")
        problem = self._argument_problem(args)
        if problem:
            return deny(problem)
        if already_allowed >= self.max_calls:
            return deny(f"over the limit of {self.max_calls} tool calls per request")
        return Decision(tool, args, scope, "allow", f"in scope ({scope}) and within limits")

    def _argument_problem(self, args: dict[str, Any]) -> str | None:
        symbols = args.get("symbols")
        if isinstance(symbols, list) and len(symbols) > self.max_symbols:
            return f"asks for {len(symbols)} symbols; the limit is {self.max_symbols} per call"
        window = args.get("window")
        if window is not None and window not in self.windows:
            return f"window {window!r} is not allowed ({_one_of(self.windows)})"
        interval = args.get("interval")
        if interval is not None and interval not in self.intervals:
            return f"interval {interval!r} is not allowed ({_one_of(self.intervals)})"
        numeric = (("depth", self.max_depth, "order-book depth"), ("limit", self.max_candles, "candle limit"))
        for key, limit, what in numeric:
            if key not in args:
                continue
            value = args[key]
            if isinstance(value, bool) or not isinstance(value, int):
                return f"{what} must be a whole number, got {value!r}"
            if value > limit:
                return f"{what} {value} is over the limit of {limit}"
        return None


def _one_of(options: tuple[str, ...]) -> str:
    return ", ".join(options[:-1]) + f" or {options[-1]}" if len(options) > 1 else options[0]


def denial_note(denied: list[dict[str, Any]]) -> str:
    """A user-facing line per blocked call, appended to the answer so it degrades visibly, not silently."""
    lines = [f"- {TOOL_LABELS.get(d['tool'], d['tool'])}: {d['reason']}" for d in denied]
    return "Some data wasn't fetched because this request's permissions don't allow it:\n" + "\n".join(lines)
