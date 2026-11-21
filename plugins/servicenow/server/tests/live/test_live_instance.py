# SPDX-License-Identifier: Apache-2.0
"""The ServiceNow server against a real instance (the plan's P1: the ITSM family was proven only on its fake).

Each test goes through the MCP protocol and the server's own client to the instance, and writes only
records it files, tagged `[choregos-live <run>]`. First run: a Personal Developer Instance on the Australia
release (`glide-australia-02-11-2026__patch7`), 2026-11-21, both as a user holding only the procedure's
role and as one holding `itil`, by basic authentication and by OAuth client credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mcp import Client

from choregos_servicenow.app import Runtime, build_server
from choregos_servicenow.config import Settings
from choregos_servicenow.query import eq

from ..conftest import Mcp
from .conftest import RUN, TAG, Admin, LiveMcp, live_env

GROUP = "Service Desk"


async def group_id(mcp: LiveMcp, name: str = GROUP) -> str:
    found = await mcp.call("search_records", table="sys_user_group", filters=[eq("name", name).model_dump()])
    assert found["count"] == 1, f"no assignment group {name!r} on the instance"
    return str(found["records"][0]["sys_id"])


def key_of(mcp: LiveMcp, what: str) -> str:
    """One idempotency key per test, authentication mode and purpose: the modes share the run's tag."""
    return f"live-{RUN}-{mcp.mode}-{what.replace(' ', '-')}"


async def new_incident(mcp: LiveMcp, what: str, **fields: str) -> dict[str, str]:
    created = await mcp.call(
        "tracker_create_item", title=f"{TAG} {what} ({mcp.mode})", body=f"Filed by the live suite, run {RUN}.",
        fields=fields, action_key=key_of(mcp, what),
    )  # fmt: skip
    assert created["created"] is True
    return created


async def twice(mcp: LiveMcp, tool: str, **arguments: str) -> list[bool]:
    """The same write made twice, as a replayed activity makes it: `written` of each."""
    return [bool((await mcp.call(tool, **arguments))["written"]) for _ in range(2)]


def near_now(iso: str) -> bool:
    moment = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return abs(datetime.now(UTC) - moment) < timedelta(minutes=10)


async def test_authentication_and_its_refusal(mode: str, mcp: Mcp) -> None:
    listed = await mcp.call("search_records", table="incident", limit=1)
    assert listed["count"] == 1
    wrong = {"SERVICENOW_PASSWORD": "not-the-password"} if mode == "basic" else {"SERVICENOW_CLIENT_SECRET": "nope"}
    refused = Mcp(build_server(Runtime(Settings.from_env(live_env(mode, **wrong)))))
    message = await refused.error("search_records", table="incident", limit=1)
    assert ("OAuth token refused (401)" if mode == "oauth" else "ServiceNow answered 401") in message


async def test_states_come_from_the_instance(runtime: Runtime) -> None:
    incident = await runtime.client.choices("incident")
    assert {"New", "In Progress", "On Hold", "Resolved", "Closed", "Canceled"} <= set(incident.values())
    # The request item has no state list of its own on a real instance: task's.
    assert {"Open", "Work in Progress", "Closed Complete"} <= set(
        (await runtime.client.choices("sc_req_item")).values()
    )
    assert {"New", "Assess", "Closed"} <= set((await runtime.client.choices("change_request")).values())


async def test_an_incident_through_the_tracker_profile(mcp: LiveMcp, admin: Admin | None) -> None:
    group = await group_id(mcp)
    created = await new_incident(mcp, "tracker profile", assignment_group=group)
    key = created["key"]
    again = await mcp.call(
        "tracker_create_item", title=f"{TAG} tracker profile ({mcp.mode})", body=f"Filed by the live suite, run {RUN}.",
        action_key=key_of(mcp, "tracker profile"),
    )  # fmt: skip
    assert again == {**created, "created": False}

    item = await mcp.call("tracker_fetch_item", key=key)
    assert (item["state"], item["fields"]["state_value"], item["item_type"]) == ("New", "1", "incident")
    assert item["fields"]["assignment_group"] == {"value": group, "display": GROUP}
    assert near_now(item["created_at"]), item["created_at"]  # stored values are UTC, labels are not
    assert item["url"].endswith(f"sys_id={created['sys_id']}")

    note = f"Run {RUN} ({mcp.mode}): triage started — accents é à ü, emoji ✅, a caret ^ORactive=false, *markdown*."
    written = await twice(mcp, "tracker_comment", key=key, markdown=note)
    status = await twice(mcp, "tracker_upsert_status", key=key, markdown="Stage: triage", marker="status")
    assert (written, status) == ([True, False], [True, False])
    assert (await mcp.call("tracker_upsert_status", key=key, markdown="Stage: review", marker="status"))["written"]

    moved = await mcp.call("tracker_set_state", key=key, status="In Progress")
    assert (moved["changed"], moved["state"]) == (True, "In Progress")
    assert key in (await mcp.call("tracker_list_candidates"))["keys"]

    item = await mcp.call("tracker_fetch_item", key=key)
    bodies = [c["body"] for c in item["comments"]]
    assert sum(1 for b in bodies if b.startswith(note)) == 1  # verbatim, once
    assert [b.split("\n")[0] for b in bodies if "[choregos-status:status]" in b] == ["Stage: triage", "Stage: review"]
    assert all(near_now(c["ts"]) for c in item["comments"]), [c["ts"] for c in item["comments"]]

    closed = await mcp.call("tracker_close", key=key)
    assert closed == {"key": key, "closed": True, "state": "Resolved"}
    assert (await mcp.call("tracker_close", key=key))["closed"] is False
    assert key not in (await mcp.call("tracker_list_candidates"))["keys"]


async def test_triage_writes_and_their_compensations(mcp: LiveMcp) -> None:
    created = await new_incident(mcp, "triage writes")
    sys_id = created["sys_id"]
    group = await group_id(mcp)

    updated = await mcp.call(
        "update_record", table="incident", sys_id=sys_id,
        fields={"impact": "1", "urgency": "1", "assignment_group": group, "category": "network"},
    )  # fmt: skip
    assert updated["previous"]["impact"] == "3"
    assert updated["current"] == {"impact": "1", "urgency": "1", "assignment_group": group, "category": "network"}
    assert updated["record"]["fields"]["priority"] == "1"  # computed by the instance, never written
    assert updated["record"]["display"]["assignment_group"] == GROUP

    # A value the instance refuses is kept silently by it (200): the server says so and writes nothing.
    refused = await mcp.error(
        "update_record", table="incident", sys_id=sys_id, fields={"urgency": "3", "category": f"no-such-{RUN}"}
    )
    assert "did not apply category" in refused
    record = await mcp.call("get_record", table="incident", sys_id=sys_id, fields=["urgency", "category"])
    assert (record["fields"]["urgency"], record["fields"]["category"]) == ("1", "network")

    restored = await mcp.call("update_record", table="incident", sys_id=sys_id, fields=updated["previous"])
    assert restored["current"] == updated["previous"]

    said = await twice(mcp, "add_comment", table="incident", sys_id=sys_id, text=f"On it ({RUN}, {mcp.mode}).")
    noted = await twice(
        mcp, "add_work_note", table="incident", sys_id=sys_id, text="Internal.", action_key=key_of(mcp, "note")
    )
    assert (said, noted) == ([True, False], [True, False])
    journal = await mcp.call("get_journal", table="incident", sys_id=sys_id)
    assert sorted(e["element"] for e in journal["entries"]) == ["comments", "work_notes"]

    resolved = await mcp.call(
        "resolve_incident", sys_id=sys_id, close_code="Solution provided", close_notes=f"Live run {RUN}."
    )
    assert (resolved["resolved"], resolved["previous_state"], resolved["state"]) == (True, "1", "6")
    reopened = await mcp.call("reopen_incident", sys_id=sys_id, state=resolved["previous_state"], reason="Undo.")
    assert reopened == {"sys_id": sys_id, "reopened": True, "state": "1"}
    await mcp.call("resolve_incident", sys_id=sys_id, close_code="Solution provided", close_notes="End of run.")


async def test_reads_the_triager_uses(mcp: LiveMcp) -> None:
    capped = await mcp.call("search_records", table="incident", limit=500)
    assert capped["count"] == 50  # the server's cap, whatever the instance holds beyond it
    widened = await mcp.call("search_records", table="incident", terms=["^ORactive=false"], limit=50)
    assert widened["count"] == 0  # the caret is escaped: a term cannot add a condition
    similar = await mcp.call("similar_incidents", terms=["email"], days=365)
    assert all(i["fields"]["state"] in ("6", "7") for i in similar["incidents"])
    articles = await mcp.call("search_knowledge", terms=["email"])
    assert all(a["fields"]["workflow_state"] == "published" for a in articles["articles"])
    ci = (await mcp.call("search_records", table="cmdb_ci", limit=1))["records"]
    if ci:
        assert (await mcp.call("get_ci", sys_id=ci[0]["sys_id"]))["sys_id"] == ci[0]["sys_id"]
        await mcp.call("list_ci_relations", sys_id=ci[0]["sys_id"])


async def test_a_change_request_is_filed_once_and_its_state_model_is_said(mcp: LiveMcp) -> None:
    arguments = {
        "short_description": f"{TAG} change", "description": "Live suite.", "implementation_plan": "Reboot.",
        "backout_plan": "None.", "test_plan": "Ping.", "action_key": key_of(mcp, "change"),
    }  # fmt: skip
    first = await mcp.call("create_change_request", **arguments)
    again = await mcp.call("create_change_request", **arguments)
    assert (first["created"], again["created"]) == (True, False)
    assert again["record"]["sys_id"] == first["record"]["sys_id"]
    key = first["record"]["fields"]["number"]
    assert (await mcp.call("tracker_fetch_item", key=key))["state"] == "New"
    # The change model refuses a state jump through the Table API: the instance's reason reaches the caller.
    refused = await mcp.error("tracker_set_state", key=key, status="Implement")
    assert "403" in refused and "Change Model" in refused, refused


async def test_a_request_item_moves_on_the_task_states(mcp: LiveMcp, admin: Admin | None) -> None:
    if admin is None:
        import pytest

        pytest.skip("filing a request item needs the cleanup user (TRANIBOR_LIVE_SERVICENOW_CLEANUP_*)")
    ritm = admin.create("sc_req_item", {"short_description": f"{TAG} request item"})
    moved = await mcp.call("tracker_set_state", key=ritm["number"], status="Work in Progress")
    assert (moved["changed"], moved["state"]) == (True, "Work in Progress")
    closed = await mcp.call("tracker_close", key=ritm["number"], close_notes="Delivered by the live suite.")
    assert closed["state"] == "Closed Complete"


async def test_tools_listed_by_the_server(mcp: LiveMcp) -> None:
    async with Client(mcp.server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert {"tracker_fetch_item", "update_record", "create_change_request"} <= names
