# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.testclient import TestClient

from choregos_microsoft_365.app import USER_TOKEN_HEADER, build_app
from choregos_microsoft_365.config import Settings
from choregos_microsoft_365.fake import FakeGraph

#: The fake tenant is seeded relative to this hour.
NOW = datetime(2026, 11, 14, 9, 17, tzinfo=UTC)
TOKEN = "platform-token-for-tests"
PERSON = "a-persons-graph-token"
#: The Host the platform's Service gives the server: not localhost, and accepted.
BASE = "http://microsoft-365.choregos-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}


def clock() -> datetime:
    return NOW


class Mcp:
    """Calls the server over HTTP as the platform does: its bearer token, and the person's token."""

    def __init__(self, http: TestClient) -> None:
        self.http = http
        self.ids = 0

    @property
    def fake(self) -> FakeGraph:
        fake = self.http.app.state.runtime.fake  # type: ignore[attr-defined]
        assert isinstance(fake, FakeGraph)
        return fake

    def rpc(self, method: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        self.ids += 1
        body = {"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params}
        response = self.http.post("/mcp", headers={**MCP_HEADERS, **(headers or {})}, json=body)
        assert response.status_code == 200, response.text
        answer = response.json()
        assert "result" in answer, answer
        result = answer["result"]
        assert isinstance(result, dict)
        return result

    def raw(self, tool: str, arguments: dict[str, Any], person: str | None = PERSON) -> dict[str, Any]:
        headers = {USER_TOKEN_HEADER: person} if person is not None else {}
        return self.rpc("tools/call", {"name": tool, "arguments": arguments}, headers)

    def call(self, tool: str, /, person: str | None = PERSON, **arguments: Any) -> dict[str, Any]:
        result = self.raw(tool, arguments, person)
        assert not result.get("isError"), result["content"]
        structured = result["structuredContent"]
        assert isinstance(structured, dict)
        return structured

    def error(self, tool: str, /, person: str | None = PERSON, **arguments: Any) -> str:
        """The typed error, `<code>: <why>`, without the SDK's `Error executing tool <tool>: `."""
        result = self.raw(tool, arguments, person)
        assert result.get("isError"), result
        text = str(result["content"][0]["text"])
        prefix = f"Error executing tool {tool}: "
        assert text.startswith(prefix), text
        return text.removeprefix(prefix)


@pytest.fixture
def http() -> Iterator[TestClient]:
    env = {"M365_FAKE": "1", "CHOREGOS_SERVER_TOKEN": TOKEN}
    app = build_app(Settings.from_env(env), clock=clock)
    with TestClient(app, base_url=BASE, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def mcp(http: TestClient) -> Mcp:
    return Mcp(http)
