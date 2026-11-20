# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult

from choregos_m365_desk.app import Runtime, build_server
from choregos_m365_desk.config import Settings
from choregos_m365_desk.fake import FakeMicrosoft

#: The core's checkout, whose normative tracker profile every answer is checked against: `TRANIBOR_CORE`,
#: else a `tranibor` checkout beside this repository's.
CORE = Path(os.environ.get("TRANIBOR_CORE") or Path(__file__).resolve().parents[5] / "tranibor")
PROFILE_SCHEMA = CORE / "packages" / "contracts" / "schemas" / "plugin-tracker-profile.schema.json"


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env({"M365_DESK_FAKE": "1"})


@pytest.fixture
def fake() -> FakeMicrosoft:
    return FakeMicrosoft()


@pytest.fixture
def runtime(settings: Settings, fake: FakeMicrosoft) -> Runtime:
    return Runtime(settings, fake=fake, sleep=no_sleep)


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


class Profile:
    """The core's tracker profile (`plugin-tracker-profile.schema.json`): a call's input, as the core's
    adapter sends it, and the server's answer, as the adapter checks it."""

    def __init__(self, schema: dict[str, Any]) -> None:
        self.validator = Draft202012Validator(schema)

    def check(self, tool: str, arguments: dict[str, Any], answer: dict[str, Any]) -> None:
        problems = [e.message for e in self.validator.iter_errors({"tool": tool, "input": arguments, "output": answer})]
        assert not problems, (tool, problems)

    def output_problems(self, tool: str, answer: dict[str, Any]) -> list[str]:
        """The answer alone against `$defs/<tool>_output` (the adapter's own check)."""
        schema = self.validator.schema
        validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{tool}_output"})
        return [e.message for e in validator.iter_errors(answer)]


SKIP_WITHOUT_CORE = (
    f"the core's tracker profile is not at {PROFILE_SCHEMA}: set TRANIBOR_CORE to a checkout of "
    "graalsystems/tranibor to check every answer against it"
)


@pytest.fixture(scope="session")
def profile() -> Profile | None:
    """The profile when the core is checked out; the tracker tests then check every call against it (and
    `test_tracker.py::test_the_core_profile_is_there` says it is skipped otherwise)."""
    if not PROFILE_SCHEMA.is_file():
        return None
    return Profile(json.loads(PROFILE_SCHEMA.read_text(encoding="utf-8")))


class Tracker:
    """A tracker tool called with the core adapter's arguments, its answer checked against the profile."""

    def __init__(self, mcp: Mcp, profile: Profile | None) -> None:
        self.mcp = mcp
        self.profile = profile
        self.checked = 0

    async def __call__(self, tool: str, /, **arguments: Any) -> dict[str, Any]:
        answer = await self.mcp.call(tool, **arguments)
        if self.profile is not None:
            self.profile.check(tool, arguments, answer)
            self.checked += 1
        return answer


@pytest.fixture
def tracker(mcp: Mcp, profile: Profile | None) -> Tracker:
    return Tracker(mcp, profile)
