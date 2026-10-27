# SPDX-License-Identifier: Apache-2.0
"""`servers/servicenow.yaml` and the server agree: what is served is declared, with its governance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from mcp import Client
from mcp.server.mcpserver import MCPServer

from choregos_servicenow.fake import sys_id_of

from .conftest import Mcp

DESCRIPTOR = Path(__file__).resolve().parents[2] / "servers" / "servicenow.yaml"
ALWAYS_A_PERSON = {"add_comment", "resolve_incident", "create_change_request"}
TRACKER_TOOLS = {
    "tracker_fetch_item",
    "tracker_create_item",
    "tracker_set_state",
    "tracker_comment",
    "tracker_upsert_status",
    "tracker_close",
    "tracker_list_candidates",
    "tracker_parse_event",
}
#: The arguments the platform fills (an idempotency key), and what undoes each write (ADR 0094).
PLATFORM_ARGUMENTS = {
    "add_work_note": {"action_key"},
    "create_change_request": {"action_key"},
    "tracker_create_item": {"action_key"},
    "tracker_comment": {"action_key"},
}
COMPENSATIONS = {
    "update_record": {
        "operation": "update_record",
        "arguments": {
            "table": "{{ result.table }}",
            "sys_id": "{{ result.sys_id }}",
            "fields": "{{ result.previous | tojson }}",
        },
    },
    "resolve_incident": {
        "operation": "reopen_incident",
        "arguments": {"sys_id": "{{ result.sys_id }}", "state": "{{ result.previous_state }}"},
    },
}


def descriptor() -> dict[str, Any]:
    loaded = yaml.safe_load(DESCRIPTOR.read_text())
    assert isinstance(loaded, dict)
    return loaded


def operations() -> dict[str, dict[str, Any]]:
    return {op["name"]: op for op in descriptor()["operations"]}


async def test_every_served_tool_is_declared_and_nothing_else(server: MCPServer) -> None:
    async with Client(server) as client:
        served = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(served) == set(operations())
    for name, op in operations().items():
        assert op["access"] in ("read", "write"), name
        schema = served[name].input_schema
        assert set(op.get("platform_arguments", [])) == PLATFORM_ARGUMENTS.get(name, set()), name
        for argument in op.get("platform_arguments", []):
            # Served, and optional: the platform validates the agent's arguments before it fills its own.
            assert argument in schema.get("properties", {}), name
            assert argument not in schema.get("required", []), name


def test_the_three_customer_visible_or_irreversible_writes_need_a_person() -> None:
    approved = {name for name, op in operations().items() if op.get("human_approval")}
    assert approved == ALWAYS_A_PERSON
    assert all(operations()[name]["access"] == "write" for name in approved)


def test_compensations_name_declared_operations() -> None:
    compensations = {name: op["compensation"] for name, op in operations().items() if "compensation" in op}
    assert compensations == COMPENSATIONS
    assert all(c["operation"] in operations() for c in compensations.values())


def test_the_rest_of_the_descriptor() -> None:
    d = descriptor()
    assert re.fullmatch(r"ghcr\.io/vargafoundation/choregos-agents/servicenow-mcp@sha256:[0-9a-f]{64}", d["image"])
    assert (d["port"], d["protocol"], d["paths"], d["implements"], d["webhook"]) == (
        8080,
        "mcp-http",
        {"mcp": "/mcp", "health": "/healthz"},
        ["tracker"],
        {"path": "/events"},
    )
    assert d["egress"] == {"from_fields": ["base_url"]}
    properties = d["config_schema"]["properties"]
    assert set(d["secret_fields"]) <= set(properties)
    assert {p["x-env"] for p in properties.values()} == {
        "SERVICENOW_BASE_URL",
        "SERVICENOW_AUTH",
        "SERVICENOW_CLIENT_ID",
        "SERVICENOW_CLIENT_SECRET",
        "SERVICENOW_USERNAME",
        "SERVICENOW_PASSWORD",
        "SERVICENOW_WEBHOOK_SECRET",
        "SERVICENOW_ASSIGNMENT_GROUPS",
    }
    assert set(operations()) >= TRACKER_TOOLS


async def test_the_declared_compensations_undo_their_write(mcp: Mcp) -> None:
    """Render each compensation's arguments with the write's result, as the platform does (every string
    through a sandboxed Jinja with undefined names refused, so each argument arrives as a string), and
    run it."""

    def render(template: dict[str, str], result: dict[str, Any]) -> dict[str, Any]:
        env = SandboxedEnvironment(undefined=StrictUndefined)
        return {key: env.from_string(value).render(result=result) for key, value in template.items()}

    sys_id = sys_id_of("INC0010010")
    before = await mcp.call("get_record", table="incident", sys_id=sys_id)
    update = await mcp.call("update_record", table="incident", sys_id=sys_id, fields={"urgency": "1", "category": "x"})
    undo = operations()["update_record"]["compensation"]
    await mcp.call(undo["operation"], **render(undo["arguments"], update))
    resolved = await mcp.call("resolve_incident", sys_id=sys_id, close_code="Solution provided", close_notes="n")
    reopen = operations()["resolve_incident"]["compensation"]["operation"]
    reopen_arguments = operations()["resolve_incident"]["compensation"]["arguments"]
    await mcp.call(reopen, **render(reopen_arguments, resolved))
    after = await mcp.call("get_record", table="incident", sys_id=sys_id)
    for name in ("urgency", "category", "state", "priority"):
        assert after["fields"][name] == before["fields"][name], name

    # A resolution that changed nothing: its compensation changes nothing either.
    noop = await mcp.call("resolve_incident", sys_id=sys_id_of("INC0010007"), close_code="c", close_notes="n")
    assert (await mcp.call(reopen, **render(reopen_arguments, noop)))["reopened"] is False
