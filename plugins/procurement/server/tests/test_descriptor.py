# SPDX-License-Identifier: Apache-2.0
"""`servers/purchasing.yaml` and the server agree: what is served is declared, with its governance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from mcp import Client
from starlette.testclient import TestClient

from tranibor_purchasing.app import build_app
from tranibor_purchasing.backends.fake import SCENES
from tranibor_purchasing.config import BACKENDS, Settings
from tranibor_purchasing.profile import OPERATIONS, READS, WRITES

from .conftest import World

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "purchasing.yaml"
COMPENSATIONS = {
    "create_supplier": "archive_supplier",
    "update_supplier": "update_supplier",
    "create_purchase_request": "cancel_purchase_request",
}


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def operations() -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in descriptor()["operations"]}


async def test_every_served_tool_is_declared_and_nothing_else(world: World) -> None:
    async with Client(world.mcp.server) as client:
        served = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(served) == set(operations()) == set(OPERATIONS)
    assert len(served) == 11
    for name, op in operations().items():
        assert op["access"] == ("read" if name in READS else "write"), name


async def test_a_tool_that_is_not_declared_does_not_exist(world: World) -> None:
    for name in ("delete_supplier", "update_contract", "create_purchase_order", "run_query", "search_records"):
        assert name not in operations()
        assert "Unknown tool" in await world.mcp.error(name)


def test_every_write_needs_a_person_and_carries_the_idempotency_key() -> None:
    for name, op in operations().items():
        if name in WRITES:
            assert op.get("human_approval") is True, name
            assert op.get("platform_arguments") == ["action_key"], name
        else:
            assert not op.get("human_approval"), name
            assert "platform_arguments" not in op and "compensation" not in op, name


async def test_the_declared_compensations_are_the_three_the_profile_names_and_fit_their_target(world: World) -> None:
    declared = {name: op["compensation"] for name, op in operations().items() if "compensation" in op}
    assert {name: c["operation"] for name, c in declared.items()} == COMPENSATIONS
    async with Client(world.mcp.server) as client:
        served = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
    for name, compensation in declared.items():
        schema = served[compensation["operation"]]
        given = set(compensation["arguments"])
        assert operations()[compensation["operation"]]["access"] == "write", name
        # Every argument it renders is one the target takes, and every one it needs it renders (the platform
        # fills the target's `action_key` itself).
        assert given <= set(schema["properties"]), name
        assert set(schema.get("required", [])) <= given, name
        assert "action_key" not in given, name


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/graalsystems/tranibor-agents/purchasing-mcp@sha256:0{64}", d["image"])
    assert (d["name"], d["port"], d["protocol"], d["paths"]) == (
        "purchasing",
        8080,
        "mcp-http",
        {"mcp": "/mcp", "health": "/healthz"},
    )
    assert "implements" not in d and "webhook" not in d
    assert d["egress"] == {"from_fields": ["base_url"]}
    properties = d["config_schema"]["properties"]
    assert set(d["secret_fields"]) == {"api_key"} <= set(properties)
    assert {p["x-env"] for p in properties.values()} == {
        "PURCHASING_BACKEND",
        "PURCHASING_BASE_URL",
        "PURCHASING_DATABASE",
        "PURCHASING_USERNAME",
        "PURCHASING_API_KEY",
    }
    # The enum is what the server names as its backends, and the fake is never one of them.
    assert properties["backend"]["enum"] == list(BACKENDS) == ["odoo"]
    assert set(d["config_schema"]["required"]) <= set(properties)
    assert "egress" in d and set(d["egress"]["from_fields"]) <= set(properties)


def test_the_declared_fake_is_what_switches_the_server_to_its_fake_and_no_administrator_reaches_it() -> None:
    """`fake` (core #1372): the platform runs an eval instance of the image with exactly these variables and its
    own token, nothing of the configuration. They switch the server to its seeded instance; none is the
    platform's (`CHOREGOS_*`) nor one a configuration field reaches."""
    d = descriptor()
    assert d["fake"] == {"env": {"PURCHASING_FAKE": "1"}, "scenes": list(SCENES)}
    variables = set(d["fake"]["env"])
    assert not {name for name in variables if name.startswith("CHOREGOS_")}
    assert not variables & {p["x-env"] for p in d["config_schema"]["properties"].values()}
    assert "PURCHASING_FAKE_SCENE" not in {p["x-env"] for p in d["config_schema"]["properties"].values()}
    as_the_platform_runs_it = {**d["fake"]["env"], "CHOREGOS_SERVER_TOKEN": "the-platform-s-token"}
    with TestClient(build_app(Settings.from_env(as_the_platform_runs_it))) as client:
        assert client.get("/healthz").json()["fake"] is True
    # Without them, the same environment is a real instance nobody configured: it refuses to start.
    with pytest.raises(ValueError, match="PURCHASING_BACKEND"):
        build_app(Settings.from_env({"CHOREGOS_SERVER_TOKEN": "the-platform-s-token"}))


@pytest.mark.parametrize("scene", SCENES)
def test_every_declared_scene_starts_on_exactly_the_fake_env_plus_the_token(scene: str) -> None:
    d = descriptor()
    assert scene in d["fake"]["scenes"]
    env = {**d["fake"]["env"], "CHOREGOS_SERVER_TOKEN": "t", "PURCHASING_FAKE_SCENE": scene}
    assert Settings.from_env(env).scene == scene
