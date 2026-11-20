# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult

from tranibor_purchasing.app import Runtime, build_server
from tranibor_purchasing.config import Settings


class Mcp:
    """Calls a tool in-process through the MCP protocol and returns its structured result.

    One client session per call: an async fixture's teardown runs in another task, which the
    session's task group refuses."""

    def __init__(self, server: MCPServer) -> None:
        self.server = server

    async def raw(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        async with Client(self.server) as client:
            return await client.call_tool(name, arguments)

    async def tool_names(self) -> set[str]:
        async with Client(self.server) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    async def call(self, tool: str, /, **arguments: Any) -> dict[str, Any]:
        result = await self.raw(tool, arguments)
        assert not result.is_error, result.content
        assert result.structured_content is not None
        return dict(result.structured_content)

    async def error(self, tool: str, /, **arguments: Any) -> str:
        result = await self.raw(tool, arguments)
        assert result.is_error, result.structured_content
        text = result.content[0]
        # The SDK prefixes a failure with `Error executing tool <name>: `; the rest is this server's message.
        return re.sub(r"^Error executing tool \w+: ", "", str(getattr(text, "text", text)))


class World:
    """One process of the server on the fake, in a scene: its runtime, its MCP client and the fake's write log."""

    def __init__(self, scene: str) -> None:
        self.runtime = Runtime(Settings.from_env({"PURCHASING_FAKE": "1", "PURCHASING_FAKE_SCENE": scene}))
        assert self.runtime.fake is not None
        self.fake = self.runtime.fake
        self.mcp = Mcp(build_server(self.runtime))


@pytest.fixture
def world() -> World:
    return World("default")


@pytest.fixture
def scene() -> Callable[[str], World]:
    return World


@pytest.fixture
def mcp(world: World) -> Mcp:
    return world.mcp
