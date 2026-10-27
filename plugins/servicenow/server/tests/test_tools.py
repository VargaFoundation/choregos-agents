# SPDX-License-Identifier: Apache-2.0
"""Every tool, through the MCP protocol, against the seeded fake instance."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from mcp import Client

from choregos_servicenow.fake import PROMPT_INJECTION, FakeServiceNow, sys_id_of

from .conftest import Mcp

READS = {"search_records", "get_record", "get_journal", "get_ci", "list_ci_relations", "search_knowledge"}
WRITES = {"add_work_note", "update_record", "add_comment", "resolve_incident", "reopen_incident"}
TRACKER = {
    "tracker_fetch_item",
    "tracker_create_item",
    "tracker_set_state",
    "tracker_comment",
    "tracker_upsert_status",
    "tracker_close",
    "tracker_list_candidates",
    "tracker_parse_event",
}


async def test_the_server_serves_exactly_the_declared_tools(mcp: Mcp) -> None:
    listed = await mcp.tool_names()
    assert listed == READS | WRITES | TRACKER | {"similar_incidents", "create_change_request"}


# ─────────────────────────── reads ───────────────────────────


async def test_search_records_with_filters_and_terms(mcp: Mcp, fake: FakeServiceNow) -> None:
    result = await mcp.call(
        "search_records",
        table="incident",
        filters=[{"field": "cmdb_ci", "op": "eq", "value": sys_id_of("ci:mail-exch-01")}],
        terms=["sync"],
    )
    numbers = {r["fields"]["number"] for r in result["records"]}
    assert numbers == {"INC0010001", "INC0010002", "INC0010009"}
    params = parse_qs(urlsplit(str(fake.requests[-1].url)).query)
    assert params["sysparm_display_value"] == ["all"]
    assert "sysparm_fields" in params
    assert params["sysparm_limit"] == ["20"]


async def test_search_records_returns_labels_next_to_values(mcp: Mcp) -> None:
    result = await mcp.call("search_records", table="incident", filters=[{"field": "number", "value": "INC0010006"}])
    record = result["records"][0]
    assert record["fields"]["state"] == "2"
    assert record["display"]["state"] == "In Progress"
    assert record["display"]["assignment_group"] == "Network"


async def test_an_injected_term_does_not_widen_the_search(mcp: Mcp) -> None:
    baseline = await mcp.call("search_records", table="incident", terms=["printer"])
    injected = await mcp.call("search_records", table="incident", terms=["printer^ORactive=true"])
    assert [r["fields"]["number"] for r in baseline["records"]] == ["INC0010011"]
    assert injected["records"] == []


async def test_the_row_cap_is_fifty(mcp: Mcp, fake: FakeServiceNow) -> None:
    await mcp.call("search_records", table="sys_choice", limit=500)
    params = parse_qs(urlsplit(str(fake.requests[-1].url)).query)
    assert params["sysparm_limit"] == ["50"]


async def test_a_table_outside_the_list_is_refused_before_any_call(mcp: Mcp, fake: FakeServiceNow) -> None:
    before = len(fake.requests)
    message = await mcp.error("search_records", table="sys_user")
    assert "not served" in message
    assert len(fake.requests) == before


async def test_a_raw_query_is_not_an_argument(mcp: Mcp, fake: FakeServiceNow) -> None:
    async with Client(mcp.server) as client:
        schemas = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
    for name, schema in schemas.items():
        assert not {"sysparm_query", "query", "encoded_query"} & set(schema.get("properties", {})), name
    # An extra argument is dropped by the SDK: it never reaches the instance.
    await mcp.call("search_records", table="incident", sysparm_query="active=false^ORactive=true", limit=1)
    sent = parse_qs(urlsplit(str(fake.requests[-1].url)).query)["sysparm_query"]
    assert sent == ["ORDERBYDESCsys_updated_on"]


async def test_get_record_by_number_and_by_sys_id(mcp: Mcp) -> None:
    by_number = await mcp.call("get_record", table="incident", number="INC0010004")
    assert by_number["fields"]["caller_id.vip"] == "true"
    assert by_number["display"]["caller_id"] == "Eleanor Vance (CFO)"
    by_id = await mcp.call("get_record", table="incident", sys_id=by_number["sys_id"], fields=["number"])
    assert by_id["fields"] == {"sys_id": by_number["sys_id"], "number": "INC0010004"}


async def test_injected_text_is_returned_as_data(mcp: Mcp) -> None:
    record = await mcp.call("get_record", table="incident", number="INC0010005")
    assert record["fields"]["description"] == PROMPT_INJECTION


async def test_get_journal(mcp: Mcp) -> None:
    result = await mcp.call("get_journal", table="incident", sys_id=sys_id_of("INC0010006"))
    assert [e["element"] for e in result["entries"]] == ["comments", "work_notes"]


async def test_get_ci_by_name_and_its_relations(mcp: Mcp) -> None:
    ci = await mcp.call("get_ci", name="app-payroll")
    assert ci["fields"]["sys_class_name"] == "cmdb_ci_appl"
    relations = await mcp.call("list_ci_relations", sys_id=ci["sys_id"])
    names = {(r["direction"], r["ci"]["name"], r["type"]) for r in relations["relations"]}
    assert names == {("outbound", "db-prod-01", "Depends on::Used by"), ("outbound", "lb-01", "Depends on::Used by")}
    inbound = await mcp.call("list_ci_relations", sys_id=sys_id_of("ci:lb-01"))
    assert {r["direction"] for r in inbound["relations"]} == {"inbound"}


async def test_search_knowledge_returns_published_articles_only(mcp: Mcp) -> None:
    result = await mcp.call("search_knowledge", terms=["VPN"])
    assert [a["fields"]["number"] for a in result["articles"]] == ["KB0010002"]


async def test_similar_incidents_from_an_incident(mcp: Mcp) -> None:
    result = await mcp.call("similar_incidents", sys_id=sys_id_of("INC0010006"))
    numbers = [i["fields"]["number"] for i in result["incidents"]]
    # Resolved 20 days ago on the same CI: found. Closed 200 days ago: outside the 90 days.
    assert numbers == ["INC0010007"]


async def test_similar_incidents_from_terms(mcp: Mcp) -> None:
    result = await mcp.call("similar_incidents", terms=["mail"])
    assert [i["fields"]["number"] for i in result["incidents"]] == ["INC0010009"]


async def test_similar_incidents_needs_something_to_compare(mcp: Mcp) -> None:
    assert "give an incident" in await mcp.error("similar_incidents")


# ─────────────────────────── writes ───────────────────────────


async def test_add_work_note_is_idempotent(mcp: Mcp, fake: FakeServiceNow) -> None:
    sys_id = sys_id_of("INC0010003")
    first = await mcp.call("add_work_note", table="incident", sys_id=sys_id, text="Asked the caller.", action_key="a1")
    again = await mcp.call("add_work_note", table="incident", sys_id=sys_id, text="Asked the caller.", action_key="a1")
    assert first["written"] is True
    assert again["written"] is False
    notes = [e for e in fake.journal(sys_id) if e["element"] == "work_notes"]
    assert len(notes) == 1
    assert notes[0]["value"].endswith("[choregos:a1]")


async def test_add_work_note_without_a_key_is_idempotent_on_its_text(mcp: Mcp, fake: FakeServiceNow) -> None:
    sys_id = sys_id_of("INC0010003")
    for _ in range(2):
        await mcp.call("add_work_note", table="incident", sys_id=sys_id, text="Same note.")
    assert len(fake.journal(sys_id)) == 1


async def test_writes_only_on_task_tables(mcp: Mcp) -> None:
    message = await mcp.error("add_work_note", table="cmdb_ci", sys_id=sys_id_of("ci:lb-01"), text="x")
    assert "writes are served on" in message


async def test_update_record_returns_previous_values_and_restores(mcp: Mcp, fake: FakeServiceNow) -> None:
    sys_id = sys_id_of("INC0010003")
    network = sys_id_of("group:Network")
    result = await mcp.call(
        "update_record", table="incident", sys_id=sys_id, fields={"impact": "1", "assignment_group": network}
    )
    assert result["previous"] == {"impact": "3", "assignment_group": sys_id_of("group:Service Desk")}
    assert result["current"] == {"impact": "1", "assignment_group": network}
    assert fake.tables["incident"][sys_id]["priority"] == "3"  # computed by the instance
    await mcp.call("update_record", table="incident", sys_id=sys_id, fields=result["previous"])
    assert fake.tables["incident"][sys_id]["impact"] == "3"
    assert fake.tables["incident"][sys_id]["assignment_group"] == sys_id_of("group:Service Desk")


async def test_priority_is_refused(mcp: Mcp, fake: FakeServiceNow) -> None:
    before = len(fake.requests)
    message = await mcp.error(
        "update_record", table="incident", sys_id=sys_id_of("INC0010003"), fields={"priority": "1"}
    )
    assert "priority is never written" in message
    assert len(fake.requests) == before


async def test_other_fields_are_refused(mcp: Mcp) -> None:
    for fields in ({"state": "6"}, {"short_description": "x"}, {"impact": "9"}, {"cmdb_ci": "not-a-sys-id"}):
        assert "refused" in await mcp.error(
            "update_record", table="incident", sys_id=sys_id_of("INC0010003"), fields=fields
        )


async def test_update_record_checks_the_group_exists(mcp: Mcp) -> None:
    message = await mcp.error(
        "update_record", table="incident", sys_id=sys_id_of("INC0010003"), fields={"assignment_group": "0" * 32}
    )
    assert "404" in message


async def test_add_comment_is_idempotent(mcp: Mcp, fake: FakeServiceNow) -> None:
    sys_id = sys_id_of("INC0010004")
    first = await mcp.call("add_comment", table="incident", sys_id=sys_id, text="We are on it.")
    again = await mcp.call("add_comment", table="incident", sys_id=sys_id, text="We are on it.")
    assert (first["written"], again["written"]) == (True, False)
    assert [e["element"] for e in fake.journal(sys_id)] == ["comments"]


async def test_resolve_then_reopen_restores_the_previous_state(mcp: Mcp, fake: FakeServiceNow) -> None:
    sys_id = sys_id_of("INC0010006")
    resolved = await mcp.call(
        "resolve_incident", sys_id=sys_id, close_code="Solution provided", close_notes="Keep-alive enabled."
    )
    assert resolved["resolved"] is True
    assert resolved["previous_state"] == "2"
    assert fake.tables["incident"][sys_id]["state"] == "6"
    assert fake.tables["incident"][sys_id]["close_notes"] == "Keep-alive enabled."
    again = await mcp.call("resolve_incident", sys_id=sys_id, close_code="x", close_notes="y")
    assert again["resolved"] is False
    reopened = await mcp.call("reopen_incident", sys_id=sys_id, state=resolved["previous_state"], reason="Undo.")
    assert reopened == {"sys_id": sys_id, "reopened": True, "state": "2"}
    noop = await mcp.call("reopen_incident", sys_id=sys_id)
    assert noop["reopened"] is False


async def test_resolve_needs_notes_and_reopen_refuses_a_closed_target(mcp: Mcp) -> None:
    sys_id = sys_id_of("INC0010007")  # resolved
    assert "close code" in await mcp.error("resolve_incident", sys_id=sys_id, close_code="", close_notes="")
    assert "cannot reopen" in await mcp.error("reopen_incident", sys_id=sys_id, state="7")


async def test_create_change_request_is_idempotent(mcp: Mcp, fake: FakeServiceNow) -> None:
    arguments = {
        "short_description": "Raise the VPN idle timeout",
        "description": "From INC0010006.",
        "implementation_plan": "Set idle timeout to 3600 s.",
        "backout_plan": "Set it back to 600 s.",
        "test_plan": "Stay idle 15 minutes.",
        "cmdb_ci": sys_id_of("ci:vpn-gw-01"),
        "action_key": "act-42",
    }
    first = await mcp.call("create_change_request", **arguments)
    again = await mcp.call("create_change_request", **arguments)
    assert first["created"] is True
    assert again["created"] is False
    assert first["record"]["sys_id"] == again["record"]["sys_id"]
    assert first["correlation_id"] == "choregos:act-42"
    assert len(fake.rows("change_request")) == 2  # the seeded one and this one
    assert first["record"]["fields"]["number"].startswith("CHG")


async def test_the_platform_action_key_is_the_change_correlation_id(mcp: Mcp, fake: FakeServiceNow) -> None:
    """The platform's key wins over whatever correlation_id the agent wrote: a replay of the same governed
    call finds the change, even when the agent changed its own correlation_id."""
    arguments = {
        "short_description": "Rotate the VPN certificate",
        "description": "From INC0010006.",
        "implementation_plan": "Install the new certificate.",
        "backout_plan": "Reinstall the old one.",
        "test_plan": "Connect from a laptop.",
    }
    first = await mcp.call("create_change_request", **arguments, correlation_id="agent-1", action_key="0123abcd")
    again = await mcp.call("create_change_request", **arguments, correlation_id="agent-2", action_key="0123abcd")
    assert first["correlation_id"] == again["correlation_id"] == "choregos:0123abcd"
    assert (first["created"], again["created"]) == (True, False)
    assert len(fake.rows("change_request")) == 2


async def test_update_record_takes_its_fields_as_json_text(mcp: Mcp) -> None:
    sys_id = sys_id_of("INC0010010")
    changed = await mcp.call("update_record", table="incident", sys_id=sys_id, fields='{"urgency": "1"}')
    assert changed["current"] == {"urgency": "1"}
    assert "JSON text" in await mcp.error("update_record", table="incident", sys_id=sys_id, fields="not json")
    assert await mcp.error("update_record", table="incident", sys_id=sys_id, fields='["urgency"]')
