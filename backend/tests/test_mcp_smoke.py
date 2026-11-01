"""Smoke test: the MCP server starts over stdio and lists every registered tool, all read-only."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.tools.registry import INPUT_MODELS

SERVER = Path(__file__).resolve().parents[1] / "mcp_server.py"


async def test_mcp_server_lists_every_tool_read_only():
    env = {**os.environ, "LLM_MODE": "offline", "TOOL_MODE": "replay", "DATABASE_URL": "sqlite://"}
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
    assert sorted(t.name for t in tools) == sorted(INPUT_MODELS)
    assert len(tools) == 8
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)
