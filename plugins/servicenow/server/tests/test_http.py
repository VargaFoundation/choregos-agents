# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface: `/mcp` (streamable HTTP), `/healthz` and the webhook `POST /events`, all but the
health probe behind the platform's bearer token (ADR 0094)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient

from choregos_servicenow.app import build_app
from choregos_servicenow.config import Settings
from choregos_servicenow.fake import sys_id_of

SECRET = "s3cret-for-tests"
TOKEN = "platform-token-for-tests"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
#: The Host the platform's Service gives the server: not localhost, and accepted.
BASE = "http://servicenow.choregos-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}


@pytest.fixture
def http() -> Iterator[TestClient]:
    env = {"SERVICENOW_FAKE": "1", "SERVICENOW_WEBHOOK_SECRET": SECRET, "CHOREGOS_SERVER_TOKEN": TOKEN}
    with TestClient(build_app(Settings.from_env(env)), base_url=BASE, headers=BEARER) as client:
        yield client


def _event(**overrides: object) -> dict[str, object]:
    return {
        "table": "incident",
        "sys_id": sys_id_of("INC0010001"),
        "number": "INC0010001",
        "sys_mod_count": 4,
        "operation": "update",
        "state": "2",
        **overrides,
    }


def test_healthz(http: TestClient) -> None:
    assert http.get("/healthz", headers={"Authorization": ""}).json() == {
        "status": "ok",
        "fake": True,
        "version": "0.1.0",
    }


def _tools_list(client: TestClient, headers: dict[str, str]) -> int:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=body).status_code


@pytest.mark.parametrize(
    "authorization",
    ["", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer{TOKEN}", TOKEN, "Basic dTpw", "Bearer "],
)
def test_mcp_without_the_platform_token_is_refused(http: TestClient, authorization: str) -> None:
    assert _tools_list(http, {"Authorization": authorization}) == 401
    assert http.post("/mcp/", headers={**MCP_HEADERS, "Authorization": authorization}, json={}).status_code == 401


def test_mcp_with_the_platform_token_answers(http: TestClient) -> None:
    assert _tools_list(http, {}) == 200


def test_events_need_the_platform_token_as_well_as_the_secret(http: TestClient) -> None:
    headers = {"X-Choregos-Secret": SECRET, "Authorization": ""}
    assert http.post("/events", headers=headers, content=json.dumps(_event())).status_code == 401


def test_the_server_refuses_to_start_without_a_token() -> None:
    for env in (
        {"SERVICENOW_FAKE": "1"},
        {"SERVICENOW_FAKE": "1", "CHOREGOS_SERVER_TOKEN": ""},
        {"SERVICENOW_FAKE": "1", "CHOREGOS_SERVER_INSECURE_DEV": "true"},
        {
            "SERVICENOW_BASE_URL": "https://x.service-now.com",
            "SERVICENOW_AUTH": "basic",
            "SERVICENOW_USERNAME": "u",
            "SERVICENOW_PASSWORD": "p",
            "CHOREGOS_SERVER_INSECURE_DEV": "1",
        },
    ):
        with pytest.raises(ValueError, match="CHOREGOS_SERVER_TOKEN"):
            build_app(Settings.from_env(env))


def test_insecure_dev_runs_the_fake_without_a_token() -> None:
    settings = Settings.from_env({"SERVICENOW_FAKE": "1", "CHOREGOS_SERVER_INSECURE_DEV": "1"})
    with TestClient(build_app(settings), base_url=BASE) as client:
        assert _tools_list(client, {}) == 200


def test_mcp_over_streamable_http(http: TestClient) -> None:
    response = http.post(
        "/mcp",
        headers=MCP_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_ci", "arguments": {"name": "lb-01"}},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["structuredContent"]["fields"]["name"] == "lb-01"


@pytest.mark.parametrize("headers", [{}, {"X-Choregos-Secret": "wrong"}, {"X-Choregos-Secret": SECRET + "x"}])
def test_events_with_a_bad_secret_are_refused(http: TestClient, headers: dict[str, str]) -> None:
    response = http.post("/events", headers=headers, content=json.dumps(_event()))
    assert response.status_code == 401


def test_events_are_normalised(http: TestClient) -> None:
    body = [
        _event(operation="insert", sys_mod_count=0),
        _event(),
        _event(state="6", sys_mod_count=5),
        _event(operation="comment", sys_mod_count=6),
        _event(table="sc_req_item", number="RITM0010001", sys_id=sys_id_of("RITM0010001"), state="3"),
        _event(table="problem", number="PRB0010001"),  # not a tracker table: ignored
    ]
    response = http.post("/events", headers={"x-choregos-secret": SECRET}, content=json.dumps(body))
    assert response.status_code == 200
    sid = sys_id_of("INC0010001")
    assert response.json() == [
        {"kind": "tracker.item.created", "item_key": "INC0010001", "delivery_id": f"{sid}:0"},
        {"kind": "tracker.item.updated", "item_key": "INC0010001", "delivery_id": f"{sid}:4"},
        {"kind": "tracker.item.closed", "item_key": "INC0010001", "delivery_id": f"{sid}:5"},
        {"kind": "tracker.item.commented", "item_key": "INC0010001", "delivery_id": f"{sid}:6"},
        {"kind": "tracker.item.closed", "item_key": "RITM0010001", "delivery_id": f"{sys_id_of('RITM0010001')}:4"},
    ]


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps(["x"]),
        json.dumps(_event(sys_id="nope")),
        json.dumps(_event(number="CHG0010001")),
        json.dumps(_event(sys_mod_count=None)),
    ],
)
def test_malformed_events_are_rejected(http: TestClient, body: str) -> None:
    response = http.post("/events", headers={"X-Choregos-Secret": SECRET}, content=body)
    assert response.status_code == 400


def test_events_without_a_configured_secret_are_refused() -> None:
    settings = Settings(
        base_url="https://x.service-now.com",
        auth="basic",
        username="u",
        password=SecretStr("p"),
        server_token=SecretStr(TOKEN),
    )
    with TestClient(build_app(settings), base_url=BASE, headers=BEARER) as client:
        response = client.post("/events", headers={"X-Choregos-Secret": ""}, content=json.dumps(_event()))
    assert response.status_code == 401
