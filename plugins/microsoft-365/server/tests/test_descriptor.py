# SPDX-License-Identifier: Apache-2.0
"""`servers/microsoft-365.yaml` and the server agree: what is served is declared, reads only, and the
connector's configuration cannot widen what the person consents to."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from starlette.testclient import TestClient

from choregos_microsoft_365.app import build_app
from choregos_microsoft_365.config import Settings

from .conftest import Mcp

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "microsoft-365.yaml"
TENANT = "0b1c2d3e-4f50-4617-8293-a4b5c6d7e8f9"
CLIENT = "11111111-2222-4333-8444-555555555555"
CONFIG: dict[str, Any] = {
    "identity_modes": ["user_consent"],
    "user_consent": {
        "authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
        "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        "client_id": CLIENT,
        "client_secret_ref": "env:CONNECTOR_MICROSOFT_365_CLIENT_SECRET",
        "scopes": ["offline_access", "Calendars.Read"],
    },
}


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def operations() -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in descriptor()["operations"]}


def errors(config: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(descriptor()["config_schema"])
    return [e.message for e in validator.iter_errors(config)]


def test_every_served_tool_is_declared_a_read_and_nothing_else(mcp: Mcp) -> None:
    served = {tool["name"] for tool in mcp.rpc("tools/list", {})["tools"]}
    assert served == set(operations()) == {"list_events_with", "search_mail_with", "search_chats_with"}
    for name, op in operations().items():
        assert op["access"] == "read", name
        assert not {"human_approval", "compensation", "platform_arguments"} & set(op), name


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/vargafoundation/choregos-agents/microsoft-365-mcp@sha256:[0-9a-f]{64}", d["image"])
    assert (d["name"], d["port"], d["protocol"], d["paths"]) == (
        "microsoft-365",
        8080,
        "mcp-http",
        {"mcp": "/mcp", "health": "/healthz"},
    )
    assert d["egress"] == {"hosts": ["graph.microsoft.com"]}
    assert "secret_fields" not in d  # the server holds no credential
    assert not {"implements", "webhook"} & set(d)


def test_the_consent_configuration_is_accepted_for_both_stages() -> None:
    assert errors(CONFIG) == []
    stage_3 = copy.deepcopy(CONFIG)
    stage_3["user_consent"]["scopes"] = ["offline_access", "Calendars.Read", "Mail.Read", "Chat.Read"]
    assert errors(stage_3) == []
    assert errors({}) == []  # a connector nobody configured yet: the server still deploys, and serves no one


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("identity_modes",), ["service"]),
        (("identity_modes",), ["user_consent", "service"]),
        (("user_consent", "scopes"), ["offline_access", "Calendars.Read", "Mail.ReadWrite"]),
        (("user_consent", "scopes"), ["offline_access", "Calendars.Read", "https://graph.microsoft.com/.default"]),
        (("user_consent", "scopes"), ["offline_access", "Mail.Read"]),
        (("user_consent", "scopes"), ["Calendars.Read"]),
        (("user_consent", "authorization_endpoint"), "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"),
        (("user_consent", "token_endpoint"), "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"),
        (("user_consent", "token_endpoint"), f"https://login.evil.example/{TENANT}/oauth2/v2.0/token"),
        (("user_consent", "client_secret_ref"), "a-secret-in-clear"),
        (("user_consent", "revocation_endpoint"), "https://login.microsoftonline.com/x"),
        (("url",), "https://graph.microsoft.com"),
    ],
)
def test_the_configuration_cannot_widen_the_consent(path: tuple[str, ...], value: Any) -> None:
    config = copy.deepcopy(CONFIG)
    target = config
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert errors(config), path


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    """`fake` (core #1372): the platform runs an eval instance with exactly these variables and its own
    token. They switch the server to its seeded tenant; none is the platform's nor one a field reaches."""
    d = descriptor()
    assert d["fake"] == {"env": {"M365_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith("CHOREGOS_")}
    reached = {p.get("x-env", name.upper()) for name, p in d["config_schema"]["properties"].items()}
    assert not variables & reached
    as_the_platform_runs_it = {**d["fake"]["env"], "CHOREGOS_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
