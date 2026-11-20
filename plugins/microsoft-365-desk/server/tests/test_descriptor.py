# SPDX-License-Identifier: Apache-2.0
"""`servers/desk.yaml` and the server agree: what is served is declared, with its governance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from mcp import Client
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from choregos_m365_desk.app import build_app
from choregos_m365_desk.config import SERVICE_HOSTS, Settings

PLUGIN = Path(__file__).resolve().parents[2]
DESCRIPTOR = PLUGIN / "servers" / "desk.yaml"
READS = {"tracker_fetch_item", "tracker_list_candidates", "tracker_parse_event"}
WRITES = {
    "tracker_create_item",
    "tracker_set_state",
    "tracker_comment",
    "tracker_upsert_status",
    "tracker_close",
    "reply",
}
#: The arguments the platform fills, never the agent.
PLATFORM_ARGUMENTS = {"tracker_comment": {"action_key"}, "reply": {"action_key", "work_item_key"}}


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def operations() -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in descriptor()["operations"]}


def test_exactly_the_tracker_profile_and_reply_with_their_access() -> None:
    access = {name: op["access"] for name, op in operations().items()}
    assert access == {**dict.fromkeys(READS, "read"), **dict.fromkeys(WRITES, "write")}
    assert len(descriptor()["operations"]) == len(access)  # none declared twice


def test_no_write_is_floored_to_a_person_the_organisation_decides() -> None:
    """`reply` waits for approval by default (a write); the organisation's policy may lower or raise it."""
    assert not [name for name, op in operations().items() if "human_approval" in op]
    assert not [name for name, op in operations().items() if "compensation" in op]


async def test_every_served_tool_is_declared_with_its_platform_arguments(server: MCPServer) -> None:
    async with Client(server) as client:
        served = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(served) == set(operations())
    for name, op in operations().items():
        assert set(op.get("platform_arguments", [])) == PLATFORM_ARGUMENTS.get(name, set()), name
        schema = served[name].input_schema
        for argument in op.get("platform_arguments", []):
            # Served, and optional: the platform validates the agent's arguments before it fills its own.
            assert argument in schema.get("properties", {}), name
            assert argument not in schema.get("required", []), name


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert d["name"] == "desk"
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/microsoft-365-desk-mcp@sha256:0{64}", d["image"])
    assert (d["port"], d["protocol"], d["paths"], d["implements"]) == (
        8080,
        "mcp-http",
        {"mcp": "/mcp", "health": "/healthz"},
        ["tracker"],
    )
    # The Bot Connector's JWT reaches tracker_parse_event (the core's ADR 0198).
    assert d["webhook"] == {"path": "/events", "forward_authorization": True}
    assert d["secret_fields"] == ["client_secret"]
    properties = d["config_schema"]["properties"]
    assert {p["x-env"] for p in properties.values()} == {
        "M365_DESK_TENANT_ID",
        "M365_DESK_CLIENT_ID",
        "M365_DESK_CLIENT_SECRET",
        "M365_DESK_MAILBOX",
        "M365_DESK_TEAMS",
        "M365_DESK_BOT_APP_ID",
        "M365_DESK_BOT_TENANT_ID",
        "M365_DESK_SERVICE_URL",
        "M365_DESK_START_LABEL",
    }
    assert d["config_schema"]["required"] == ["tenant_id", "client_id", "client_secret"]


def test_egress_is_microsoft_and_nothing_else() -> None:
    hosts = descriptor()["egress"]["hosts"]
    assert hosts == ["graph.microsoft.com", "login.microsoftonline.com", "login.botframework.com", *SERVICE_HOSTS]
    # The configured service URL can only name an allowed host: the schema's pattern says so too.
    assert descriptor()["config_schema"]["properties"]["service_url"]["pattern"].startswith(
        r"^https://smba\.trafficmanager\.net/"
    )


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    """`fake` (core #1372): the platform runs an eval instance with exactly these variables and its own
    token, nothing of the configuration. They switch the server to its seeded tenant; none is the
    platform's (`CHOREGOS_*`) nor one a configuration field reaches."""
    d = descriptor()
    assert d["fake"] == {"env": {"M365_DESK_FAKE": "1"}, "scenes": ["default"]}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith(("CHOREGOS_", "TRANIBOR_"))}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "CHOREGOS_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    # Without them, the same environment is a real tenant nobody configured: it refuses to start.
    with pytest.raises(ValueError, match="M365_DESK_TENANT_ID"):
        build_app(Settings.from_env({"CHOREGOS_SERVER_TOKEN": "the-platform-s-token"}))


def test_the_plugin_requires_the_first_core_that_forwards_authorization() -> None:
    manifest = yaml.safe_load((PLUGIN / "plugin.yaml").read_text())
    assert manifest["metadata"]["core"] == ">=0.28.0,<1.0.0"
    assert manifest["contents"]["servers"] == ["servers/desk.yaml"]


def test_tracker_comment_says_it_writes_nothing() -> None:
    """The platform's comments are for its own readers: neither the declaration nor the tool may promise a
    reply to the requester."""
    description = operations()["tracker_comment"]["description"].lower()
    assert "reply" not in description and "requester" in description and "nothing" in description
    from choregos_m365_desk import app

    source = Path(app.__file__).read_text()
    docstring = source.split("async def tracker_comment", 1)[1].split('"""')[1].lower()
    assert "reply" not in docstring.replace("`reply`", "") and "nothing" in docstring
