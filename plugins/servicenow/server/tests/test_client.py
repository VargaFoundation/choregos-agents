# SPDX-License-Identifier: Apache-2.0
"""The client against a ServiceNow answered by respx: OAuth, basic authentication, the request rules."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from choregos_servicenow import client as client_module
from choregos_servicenow.client import ServiceNowClient
from choregos_servicenow.config import Settings
from choregos_servicenow.errors import Refused, ServiceNowError
from choregos_servicenow.query import Query, eq

BASE = "https://acme.service-now.com"
ROW = {"sys_id": {"value": "a" * 32, "display_value": "a" * 32}, "number": {"value": "INC1", "display_value": "INC1"}}


def oauth_settings() -> Settings:
    return Settings(base_url=BASE, auth="oauth", client_id="cid", client_secret=SecretStr("csecret"))


@respx.mock
async def test_oauth_token_is_cached_until_it_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr("choregos_servicenow.client.time.monotonic", lambda: clock[0])
    tokens = iter(["t1", "t2"])
    token_route = respx.post(f"{BASE}/oauth_token.do").mock(
        side_effect=lambda request: httpx.Response(200, json={"access_token": next(tokens), "expires_in": 1800})
    )
    table = respx.get(f"{BASE}/api/now/table/incident").mock(return_value=httpx.Response(200, json={"result": [ROW]}))
    client = ServiceNowClient(oauth_settings())

    await client.list_records("incident", Query("incident").where(eq("active", "true")))
    await client.list_records("incident", Query("incident"))
    assert token_route.call_count == 1
    form = token_route.calls[0].request.content.decode()
    assert "grant_type=client_credentials" in form and "client_secret=csecret" in form
    assert table.calls[-1].request.headers["Authorization"] == "Bearer t1"

    clock[0] += 1800 - client_module.TOKEN_MARGIN_S + 1  # past the renewal margin
    await client.list_records("incident", Query("incident"))
    assert token_route.call_count == 2
    assert table.calls[-1].request.headers["Authorization"] == "Bearer t2"
    await client.aclose()


@respx.mock
async def test_a_rejected_token_is_renewed_once() -> None:
    tokens = iter(["old", "new"])
    respx.post(f"{BASE}/oauth_token.do").mock(
        side_effect=lambda request: httpx.Response(200, json={"access_token": next(tokens), "expires_in": 1800})
    )

    def answer(request: httpx.Request) -> httpx.Response:
        if request.headers["Authorization"] == "Bearer old":
            return httpx.Response(401, json={"error": {"message": "token revoked"}})
        return httpx.Response(200, json={"result": [ROW]})

    respx.get(f"{BASE}/api/now/table/incident").mock(side_effect=answer)
    client = ServiceNowClient(oauth_settings())
    rows = await client.list_records("incident", Query("incident"))
    assert rows[0]["fields"]["number"] == "INC1"
    await client.aclose()


@respx.mock
async def test_a_refused_client_secret_is_an_error() -> None:
    respx.post(f"{BASE}/oauth_token.do").mock(return_value=httpx.Response(401, json={"error": "access_denied"}))
    client = ServiceNowClient(oauth_settings())
    with pytest.raises(ServiceNowError, match="OAuth token refused"):
        await client.list_records("incident", Query("incident"))
    await client.aclose()


@respx.mock
async def test_basic_auth_and_the_parameters_of_every_read() -> None:
    route = respx.get(f"{BASE}/api/now/table/incident").mock(return_value=httpx.Response(200, json={"result": []}))
    settings = Settings(base_url=BASE, auth="basic", username="svc", password=SecretStr("pw"))
    client = ServiceNowClient(settings)
    await client.list_records("incident", Query("incident").where(eq("short_description", "a^ORb")), limit=999)
    request = route.calls[0].request
    assert request.headers["Authorization"].startswith("Basic ")
    params = request.url.params
    assert params["sysparm_query"] == "short_description=a^^ORb"
    assert params["sysparm_display_value"] == "all"
    assert params["sysparm_limit"] == "50"
    assert params["sysparm_fields"].split(",")[:2] == ["sys_id", "number"]
    await client.aclose()


async def test_a_table_outside_the_list_never_reaches_the_network() -> None:
    settings = Settings(base_url=BASE, auth="basic", username="svc", password=SecretStr("pw"))
    client = ServiceNowClient(settings, transport=httpx.MockTransport(lambda r: pytest.fail("network reached")))
    with pytest.raises(Refused):
        await client.list_records("sys_user", Query("incident"))
    with pytest.raises(Refused):
        await client.get("sys_properties", "a" * 32)
    await client.aclose()


def test_configuration_from_the_environment() -> None:
    settings = Settings.from_env(
        {
            "SERVICENOW_BASE_URL": BASE,
            "SERVICENOW_AUTH": "oauth",
            "SERVICENOW_CLIENT_ID": "cid",
            "SERVICENOW_CLIENT_SECRET": "cs",
            "SERVICENOW_WEBHOOK_SECRET": "ws",
            "SERVICENOW_ASSIGNMENT_GROUPS": "Service Desk, Network ,",
        }
    )
    assert settings.assignment_groups == ("Service Desk", "Network")
    assert settings.client_secret is not None and "cs" not in repr(settings)
    with pytest.raises(ValueError, match="CLIENT_SECRET"):
        Settings.from_env({"SERVICENOW_BASE_URL": BASE, "SERVICENOW_AUTH": "oauth", "SERVICENOW_CLIENT_ID": "x"})
    with pytest.raises(ValueError, match="https"):
        Settings.from_env({"SERVICENOW_BASE_URL": "http://x", "SERVICENOW_AUTH": "basic", "SERVICENOW_USERNAME": "u",
                           "SERVICENOW_PASSWORD": "p"})  # fmt: skip
    with pytest.raises(ValueError, match="oauth or basic"):
        Settings.from_env({"SERVICENOW_BASE_URL": BASE, "SERVICENOW_AUTH": "ntlm"})
