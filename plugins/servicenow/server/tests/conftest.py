# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Any

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult

from choregos_servicenow.app import Runtime, build_server
from choregos_servicenow.config import Settings
from choregos_servicenow.fake import FakeServiceNow


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env({"SERVICENOW_FAKE": "1"})


@pytest.fixture
def runtime(settings: Settings) -> Runtime:
    return Runtime(settings)


@pytest.fixture
def fake(runtime: Runtime) -> FakeServiceNow:
    assert runtime.fake is not None
    return runtime.fake


@pytest.fixture
def server(runtime: Runtime) -> MCPServer:
    return build_server(runtime)


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
        return str(getattr(text, "text", text))


@pytest.fixture
def mcp(server: MCPServer) -> Mcp:
    return Mcp(server)
