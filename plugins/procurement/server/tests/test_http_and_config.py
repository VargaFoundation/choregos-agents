# SPDX-License-Identifier: Apache-2.0
"""The HTTP surface (`/mcp` behind the platform's bearer token, `/healthz` open) and what it will not start on."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from tranibor_purchasing import __version__
from tranibor_purchasing.app import build_app
from tranibor_purchasing.backends.fake import FAKE_TODAY, FakePurchasing, scene_name
from tranibor_purchasing.config import Settings

TOKEN = "platform-token-for-tests"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
#: The Host the platform's Service gives the server: not localhost, and accepted.
BASE = "http://purchasing.choregos-plugins.svc:8080"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}
FAKE_ENV = {"PURCHASING_FAKE": "1", "CHOREGOS_SERVER_TOKEN": TOKEN}


@pytest.fixture
def http() -> Iterator[TestClient]:
    with TestClient(build_app(Settings.from_env(FAKE_ENV)), base_url=BASE, headers=BEARER) as client:
        yield client


def _call(client: TestClient, headers: dict[str, str], name: str = "get_supplier", **arguments: object) -> int:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    return client.post("/mcp", headers={**MCP_HEADERS, **headers}, json=body).status_code


def test_healthz_is_open(http: TestClient) -> None:
    assert http.get("/healthz", headers={"Authorization": ""}).json() == {
        "status": "ok",
        "fake": True,
        "version": __version__,
    }


@pytest.mark.parametrize(
    "authorization",
    ["", f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}", f"bearer{TOKEN}", TOKEN, "Basic dTpw", "Bearer "],
)
def test_mcp_without_the_exact_bearer_token_is_refused(http: TestClient, authorization: str) -> None:
    assert _call(http, {"Authorization": authorization}, supplier_id="SUP-0001") == 401


def test_mcp_over_streamable_http(http: TestClient) -> None:
    response = http.post(
        "/mcp",
        headers=MCP_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_supplier", "arguments": {"supplier_id": "SUP-0003"}},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["supplier"]["name"] == "Northfield Print Services SARL"


def test_a_bank_detail_is_refused_over_http_too(http: TestClient) -> None:
    response = http.post(
        "/mcp",
        headers=MCP_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "create_supplier",
                "arguments": {"name": "X", "country": "FR", "iban": "XX00TESTTESTTEST0000", "action_key": "k"},
            },
        },
    )
    result = response.json()["result"]
    assert result["isError"] is True and "bank detail" in result["content"][0]["text"]
    assert http.app.state.runtime.fake.writes == []  # type: ignore[attr-defined]


def test_without_the_platforms_token_the_server_refuses_to_start() -> None:
    for env in ({"PURCHASING_FAKE": "1"}, {"PURCHASING_FAKE": "1", "CHOREGOS_SERVER_INSECURE_DEV": "true"}):
        with pytest.raises(ValueError, match="CHOREGOS_SERVER_TOKEN"):
            build_app(Settings.from_env(env))


def test_insecure_dev_runs_the_fake_without_a_token() -> None:
    settings = Settings.from_env({"PURCHASING_FAKE": "1", "CHOREGOS_SERVER_INSECURE_DEV": "1"})
    with TestClient(build_app(settings), base_url=BASE) as client:
        assert _call(client, {}, supplier_id="SUP-0001") == 200


def test_odoo_starts_the_server_without_opening_a_connection() -> None:
    import respx

    from .odoo_script import KEY, ODOO_ENV

    with respx.mock(assert_all_called=False) as router:  # any request would raise: the mock has no route
        app = build_app(Settings.from_env(ODOO_ENV))
    assert not router.calls and app.state.runtime.backend.name == "odoo" and app.state.runtime.fake is None
    settings = Settings.from_env(ODOO_ENV)
    assert KEY not in repr(settings) and KEY not in settings.model_dump_json()
    assert settings.base_url == "https://odoo.example.test"  # no trailing slash, whatever was typed
    assert (
        Settings.from_env({**ODOO_ENV, "PURCHASING_BASE_URL": "https://odoo.example.test/"}).base_url
        == settings.base_url
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"PURCHASING_BASE_URL": "http://odoo.example.test"}, "https URL"),
        ({"PURCHASING_BASE_URL": ""}, "https URL"),
        ({"PURCHASING_BASE_URL": "https://user:pw@odoo.example.test"}, "no credential"),
        ({"PURCHASING_BASE_URL": "https://odoo.example.test/?db=x"}, "no credential, query or fragment"),
        ({"PURCHASING_USERNAME": ""}, "PURCHASING_USERNAME"),
        ({"PURCHASING_API_KEY": ""}, "PURCHASING_API_KEY"),
    ],
)
def test_an_incomplete_or_unsafe_odoo_connection_refuses_to_start(change: dict[str, str], message: str) -> None:
    from .odoo_script import ODOO_ENV

    with pytest.raises(ValueError, match=message):
        Settings.from_env({**ODOO_ENV, **change})


def test_the_database_is_optional_and_no_other_backend_than_the_listed_ones_starts() -> None:
    from .odoo_script import ODOO_ENV

    assert Settings.from_env({k: v for k, v in ODOO_ENV.items() if k != "PURCHASING_DATABASE"}).database is None
    with pytest.raises(ValueError, match="unknown"):
        Settings.from_env({**ODOO_ENV, "PURCHASING_BACKEND": "sap"})


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"PURCHASING_BACKEND": "fake"}, "unknown"),
        ({"PURCHASING_BACKEND": "sap"}, "unknown"),
        ({}, "PURCHASING_BACKEND is not set"),
    ],
)
def test_the_fake_is_not_a_backend_an_administrator_can_pick(env: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings.from_env({**env, "CHOREGOS_SERVER_TOKEN": TOKEN})


def test_an_unknown_scene_is_refused() -> None:
    with pytest.raises(ValueError, match="PURCHASING_FAKE_SCENE"):
        Settings.from_env({**FAKE_ENV, "PURCHASING_FAKE_SCENE": "chaos"})
    assert scene_name(" Expiring_Contracts ") == "expiring-contracts"


async def test_the_fakes_clock_is_fixed_and_its_state_is_per_process() -> None:
    assert FAKE_TODAY.isoformat() == "2026-11-20"
    one, two = FakePurchasing(), FakePurchasing()
    await one.archive_supplier("SUP-0001", None)
    assert two.suppliers["SUP-0001"].status == "active"
