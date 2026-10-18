from __future__ import annotations

from typing import Literal

ToolErrorCode = Literal[
    "unknown_symbol", "timeout", "upstream_error", "empty_data", "invalid_input", "fixture_missing"
]


class ToolError(Exception):
    """A tool failure with a user-presentable message. Never carries made-up data."""

    def __init__(self, code: ToolErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
