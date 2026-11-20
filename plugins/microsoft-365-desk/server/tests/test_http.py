# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` (streamable HTTP), `/healthz` and `/events`, all but the health probe behind
the platform's bearer token (ADR 0094); and the configuration the server refuses to start with."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from choregos_m365_desk.app import build_app
from choregos_m365_desk.config import Settings
from choregos_m365_desk.fake import MAIL_PLAIN

from .test_descriptor import operations

TOKEN = "platform-token-for-tests"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
#: The Host the platform's Service gives the server: not localhost, and accepted.
BASE = "http://desk.choregos-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}
REAL = {
    "M365_DESK_TENANT_ID": "0f6f2f1c-1111-4222-8333-444455556666",
    "M365_DESK_CLIENT_ID": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    "M365_DESK_CLIENT_SECRET": "s",
    "M365_DESK_MAILBOX": "it-desk@contoso.com",
    "CHOREGOS_SERVER_TOKEN": TOKEN,
}


@pytest.fixture
def http() -> Iterator[TestClient]:
    env = {"M365_DESK_FAKE": "1", "CHOREGOS_SERVER_TOKEN": TOKEN}
    with TestClient(build_app(Settings.from_env(env)), base_url=BASE, headers=BEARER) as client:
        yield client


def test_healthz_is_open(http: TestClient) -> None:
    response = http.get("/healthz", headers={"Authorization": ""})
    assert response.json() == {"status": "ok", "fake": True, "version": "0.1.0"}


@pytest.mark.parametrize(
    "authorization",
    ["", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer{TOKEN}", TOKEN, "Basic dTpw", "Bearer "],
)
def test_mcp_and_events_without_the_platform_token_are_refused(http: TestClient, authorization: str) -> None:
    headers = {"Authorization": authorization}
    assert http.post("/mcp", headers={**MCP_HEADERS, **headers}, json={}).status_code == 401
    assert http.post("/events", headers=headers, content=json.dumps({"headers": {}, "body": ""})).status_code == 401


def test_tools_list_is_exactly_the_declared_operations(http: TestClient) -> None:
    response = http.post(
        "/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    assert response.status_code == 200
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == set(operations())


def test_a_tool_over_streamable_http(http: TestClient) -> None:
    response = http.post(
        "/mcp",
        headers=MCP_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "tracker_fetch_item", "arguments": {"key": f"mail:{MAIL_PLAIN}"}},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["title"] == "VPN token expired"


def test_the_server_refuses_to_start_without_a_token() -> None:
    for env in (
        {"M365_DESK_FAKE": "1"},
        {"M365_DESK_FAKE": "1", "CHOREGOS_SERVER_TOKEN": ""},
        {"M365_DESK_FAKE": "1", "CHOREGOS_SERVER_INSECURE_DEV": "true"},
        {**REAL, "CHOREGOS_SERVER_TOKEN": "", "CHOREGOS_SERVER_INSECURE_DEV": "1"},
    ):
        with pytest.raises(ValueError, match="CHOREGOS_SERVER_TOKEN"):
            build_app(Settings.from_env(env))


def test_insecure_dev_runs_the_fake_without_a_token() -> None:
    settings = Settings.from_env({"M365_DESK_FAKE": "1", "CHOREGOS_SERVER_INSECURE_DEV": "1"})
    with TestClient(build_app(settings), base_url=BASE) as client:
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert client.post("/mcp", headers=MCP_HEADERS, json=body).status_code == 200


def test_a_real_configuration_starts_and_reads_its_variables() -> None:
    teams = [{"channel_id": "19:abc@thread.tacv2", "team_id": "12345678-1234-4123-8123-123456789abc"}]
    settings = Settings.from_env(
        {
            **REAL,
            "M365_DESK_TEAMS": json.dumps(teams),
            "M365_DESK_BOT_APP_ID": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "M365_DESK_BOT_TENANT_ID": REAL["M365_DESK_TENANT_ID"],
            "M365_DESK_SERVICE_URL": "https://smba.trafficmanager.net/emea/",
            "M365_DESK_START_LABEL": "desk-ready",
        }
    )
    assert (settings.mailbox, settings.teams[0].channel_id, settings.bot_id) == (
        "it-desk@contoso.com",
        "19:abc@thread.tacv2",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    assert (settings.service_url, settings.start_label, settings.fake) == (
        "https://smba.trafficmanager.net/emea/",
        "desk-ready",
        False,
    )
    assert Settings.from_env(REAL).bot_id == REAL["M365_DESK_CLIENT_ID"]
    assert Settings.from_env(REAL).bot_tenant_id == "botframework.com"
    with TestClient(build_app(Settings.from_env(REAL)), base_url=BASE) as client:
        assert client.get("/healthz").json()["fake"] is False


@pytest.mark.parametrize(
    ("override", "why"),
    [
        ({"M365_DESK_MAILBOX": ""}, "at least one channel"),
        ({"M365_DESK_TENANT_ID": "contoso.onmicrosoft.com"}, "TENANT_ID"),
        ({"M365_DESK_CLIENT_ID": ""}, "CLIENT_ID"),
        ({"M365_DESK_CLIENT_SECRET": ""}, "CLIENT_SECRET"),
        ({"M365_DESK_MAILBOX": "not an address"}, "MAILBOX"),
        ({"M365_DESK_TEAMS": "{not json"}, "M365_DESK_TEAMS"),
        ({"M365_DESK_TEAMS": json.dumps([{"team_id": "x", "channel_id": "19:a@thread.tacv2"}])}, "team_id"),
        ({"M365_DESK_TEAMS": json.dumps([{"team_id": REAL["M365_DESK_TENANT_ID"], "channel_id": "c"}])}, "channel"),
        ({"M365_DESK_BOT_APP_ID": "bot"}, "BOT_APP_ID"),
        ({"M365_DESK_BOT_TENANT_ID": "common"}, "BOT_TENANT_ID"),
        ({"M365_DESK_SERVICE_URL": "https://smba.example.test/teams/"}, "SERVICE_URL"),
        ({"M365_DESK_SERVICE_URL": "http://smba.trafficmanager.net/teams/"}, "SERVICE_URL"),
        ({"M365_DESK_START_LABEL": "a,b\n"}, "START_LABEL"),
    ],
)
def test_a_configuration_that_does_not_hold_refuses_to_start(override: dict[str, str], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        Settings.from_env({**REAL, **override})


def test_the_client_secret_never_shows_in_a_refusal() -> None:
    with pytest.raises(ValueError) as refused:
        Settings.from_env({**REAL, "M365_DESK_CLIENT_SECRET": "very-secret-value", "M365_DESK_MAILBOX": ""})
    assert "very-secret-value" not in str(refused.value)
