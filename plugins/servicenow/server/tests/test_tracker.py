# SPDX-License-Identifier: Apache-2.0
"""The tracker profile (TRACKER_PROFILE.md), through MCP, against the fake."""

from __future__ import annotations

import json

from choregos_servicenow.fake import FakeServiceNow, sys_id_of

from .conftest import Mcp


async def test_fetch_item_maps_an_incident_to_a_work_item(mcp: Mcp) -> None:
    item = await mcp.call("tracker_fetch_item", key="INC0010006")
    assert item["key"] == "INC0010006"
    assert item["title"] == "VPN disconnects every 10 minutes"
    assert item["state"] == "In Progress"
    assert item["item_type"] == "incident"
    assert item["author"] == "Abel Tuter"
    assert item["url"] == (f"https://fake.service-now.test/nav_to.do?uri=incident.do?sys_id={sys_id_of('INC0010006')}")
    assert item["created_at"].endswith("Z")
    assert item["fields"]["assignment_group"] == {"value": sys_id_of("group:Network"), "display": "Network"}
    assert [c["visibility"] for c in item["comments"]] == ["internal", "customer"]  # oldest first


async def test_fetch_item_flags_a_vip_caller(mcp: Mcp) -> None:
    assert (await mcp.call("tracker_fetch_item", key="INC0010004"))["fields"]["caller_vip"] is True
    assert (await mcp.call("tracker_fetch_item", key="INC0010003"))["fields"]["caller_vip"] is False


async def test_fetch_item_on_request_items_and_changes(mcp: Mcp) -> None:
    assert (await mcp.call("tracker_fetch_item", key="RITM0010001"))["item_type"] == "sc_req_item"
    assert (await mcp.call("tracker_fetch_item", key="CHG0010001"))["state"] == "Closed"
    assert "not an incident" in await mcp.error("tracker_fetch_item", key="PRB0010001")
    assert "no incident" in await mcp.error("tracker_fetch_item", key="INC9999999")


async def test_create_item_is_idempotent_and_never_takes_priority(mcp: Mcp, fake: FakeServiceNow) -> None:
    first = await mcp.call("tracker_create_item", title="Disk full", body="On db-prod-01.", action_key="k1")
    again = await mcp.call("tracker_create_item", title="Disk full", body="On db-prod-01.", action_key="k1")
    assert first["created"] is True
    assert again == {**first, "created": False}
    assert first["key"].startswith("INC")
    assert "priority" in await mcp.error("tracker_create_item", title="x", body="y", fields={"priority": "1"})
    assert "not accepted" in await mcp.error("tracker_create_item", title="x", body="y", fields={"state": "6"})


async def test_set_state_by_label(mcp: Mcp, fake: FakeServiceNow) -> None:
    result = await mcp.call("tracker_set_state", key="INC0010003", status="on hold", label="triaged")
    assert result == {"key": "INC0010003", "changed": True, "state": "On Hold", "ignored": ["label"]}
    assert fake.tables["incident"][sys_id_of("INC0010003")]["state"] == "3"
    again = await mcp.call("tracker_set_state", key="INC0010003", status="On Hold")
    assert again["changed"] is False
    assert "not a state" in await mcp.error("tracker_set_state", key="INC0010003", status="Done-ish")
    assert "tracker_close" in await mcp.error("tracker_set_state", key="INC0010003", status="Resolved")


async def test_comment_is_an_internal_work_note_and_idempotent(mcp: Mcp, fake: FakeServiceNow) -> None:
    first = await mcp.call("tracker_comment", key="INC0010011", markdown="Run started.", action_key="c1")
    again = await mcp.call("tracker_comment", key="INC0010011", markdown="Run started.", action_key="c1")
    assert first == {"key": "INC0010011", "id": "[choregos:c1]", "written": True}
    assert again["written"] is False
    assert [e["element"] for e in fake.journal(sys_id_of("INC0010011"))] == ["work_notes"]


async def test_upsert_status_adds_a_note_only_when_the_status_changes(mcp: Mcp, fake: FakeServiceNow) -> None:
    sys_id = sys_id_of("INC0010011")
    assert (await mcp.call("tracker_upsert_status", key="INC0010011", markdown="Triage", marker="s"))["written"]
    assert not (await mcp.call("tracker_upsert_status", key="INC0010011", markdown="Triage", marker="s"))["written"]
    assert (await mcp.call("tracker_upsert_status", key="INC0010011", markdown="Review", marker="s"))["written"]
    assert len(fake.journal(sys_id)) == 2
    item = await mcp.call("tracker_fetch_item", key="INC0010011")
    assert {c["marker"] for c in item["comments"]} == {"s"}


async def test_close_each_kind_of_item_once(mcp: Mcp, fake: FakeServiceNow) -> None:
    closed = await mcp.call("tracker_close", key="INC0010011")
    assert closed == {"key": "INC0010011", "closed": True, "state": "Resolved"}
    incident = fake.tables["incident"][sys_id_of("INC0010011")]
    assert (incident["state"], incident["close_code"]) == ("6", "Solution provided")
    assert (await mcp.call("tracker_close", key="INC0010011"))["closed"] is False
    ritm = await mcp.call("tracker_close", key="RITM0010001", close_notes="Delivered.")
    assert ritm["state"] == "Closed Complete"
    assert (await mcp.call("tracker_close", key="CHG0010001"))["closed"] is False  # already closed


async def test_list_candidates_reads_the_configured_queue(mcp: Mcp) -> None:
    result = await mcp.call("tracker_list_candidates")
    # Service Desk (the fake's default queue): open incidents, then request items, each oldest first.
    assert result["keys"] == [
        "INC0010011",
        "INC0010001",
        "INC0010002",
        "INC0010003",
        "INC0010004",
        "INC0010005",
        "INC0012399",
        "RITM0010001",
    ]
    only = await mcp.call("tracker_list_candidates", tables=["sc_req_item"])
    assert only["keys"] == ["RITM0010001"]
    assert "not a tracker table" in await mcp.error("tracker_list_candidates", tables=["problem"])


async def test_parse_event_verifies_then_normalises(mcp: Mcp) -> None:
    body = json.dumps(
        {"table": "incident", "sys_id": sys_id_of("INC0010001"), "number": "INC0010001", "sys_mod_count": 3}
    )
    good = await mcp.call("tracker_parse_event", headers={"X-Choregos-Secret": "fake-webhook-secret"}, body=body)
    assert good == {
        "verified": True,
        "events": [
            {"kind": "tracker.item.updated", "item_key": "INC0010001", "delivery_id": f"{sys_id_of('INC0010001')}:3"}
        ],
    }
    bad = await mcp.call("tracker_parse_event", headers={"X-Choregos-Secret": "nope"}, body=body)
    assert bad == {"verified": False, "events": []}


# ───── what a real instance does that the fake did not (an Australia PDI, 2026-11-21) ─────


async def test_a_request_item_takes_the_task_states(mcp: Mcp, fake: FakeServiceNow) -> None:
    """A real instance holds the request item's states on `task`: reading `sc_req_item` alone found none,
    so every state of a request item was refused and it could not be closed."""
    assert not [c for c in fake.rows("sys_choice") if c["name"] == "sc_req_item" and c["element"] == "state"]
    moved = await mcp.call("tracker_set_state", key="RITM0010001", status="Work in Progress")
    assert moved["changed"] is True
    assert fake.tables["sc_req_item"][sys_id_of("RITM0010001")]["state"] == "2"
    closed = await mcp.call("tracker_close", key="RITM0010001", close_notes="Delivered.")
    assert closed == {"key": "RITM0010001", "closed": True, "state": "Closed Complete"}


async def test_states_without_sys_choice_are_the_out_of_box_ones(mcp: Mcp, fake: FakeServiceNow) -> None:
    """`sys_choice` refuses a user with `itil` and not the plugin's role (403): the states are then
    ServiceNow's own, not an error on every transition."""
    fake.choices_readable = False
    result = await mcp.call("tracker_set_state", key="INC0010003", status="On Hold")
    assert result["state"] == "On Hold"
    assert fake.tables["incident"][sys_id_of("INC0010003")]["state"] == "3"
    ritm = await mcp.call("tracker_close", key="RITM0010001")
    assert ritm["state"] == "Closed Complete"


async def test_an_unreadable_journal_does_not_write_twice(mcp: Mcp, fake: FakeServiceNow) -> None:
    """`sys_journal_field` answers no row to a user without its read ACL: the work note's marker was never
    found, and a replayed comment or status wrote a second note. The record's own journal fields are read."""
    fake.journal_readable = False
    fake.tz_offset_hours = -8  # the instance's users read Pacific time by default
    sys_id = sys_id_of("INC0010011")
    first = await mcp.call("tracker_comment", key="INC0010011", markdown="Run started.", action_key="c1")
    again = await mcp.call("tracker_comment", key="INC0010011", markdown="Run started.", action_key="c1")
    assert (first["written"], again["written"]) == (True, False)
    assert [(await mcp.call("tracker_upsert_status", key="INC0010011", markdown="v1", marker="s"))["written"]
            for _ in range(2)] == [True, False]  # fmt: skip
    said = [(await mcp.call("add_comment", table="incident", sys_id=sys_id, text="We are on it."))["written"]
            for _ in range(2)]  # fmt: skip
    assert said == [True, False]
    notes = [e for e in fake.journal(sys_id) if e["value"].startswith(("Run started.", "v1", "We are on it."))]
    assert len(notes) == 3

    item = await mcp.call("tracker_fetch_item", key="INC0010011")
    ours = [c for c in item["comments"] if c["body"].startswith(("Run started.", "v1", "We are on it."))]
    assert [c["marker"] for c in ours if c["visibility"] == "internal"] == ["c1", "s"]
    assert [c["visibility"] for c in ours].count("customer") == 1
    stored = {e["value"].split("\n")[0]: e["sys_created_on"] for e in notes}
    # The header's local time, back in UTC: the same instant as the stored entry.
    assert {c["body"].split("\n")[0]: c["ts"] for c in ours} == {
        k: v.replace(" ", "T") + "Z" for k, v in stored.items()
    }


async def test_a_creation_whose_key_the_instance_dropped_says_so(mcp: Mcp, fake: FakeServiceNow) -> None:
    """An `itil` user cannot create `correlation_id`: ServiceNow files the incident without it, and a
    replay files a second one. The answer says so (the platform would retry a failure, filing more)."""
    fake.unwritable = {("incident", "correlation_id"), ("change_request", "correlation_id")}
    created = await mcp.call("tracker_create_item", title="Disk full", body="On db-prod-01.", action_key="k1")
    assert created["created"] is True
    assert created["idempotent"] is False
    assert "incident.correlation_id" in created["warning"]
    change = await mcp.call(
        "create_change_request", short_description="Reboot", description="d", implementation_plan="i",
        backout_plan="b", test_plan="t", action_key="c1",
    )  # fmt: skip
    assert change["idempotent"] is False
    fake.unwritable = set()
    kept = await mcp.call("tracker_create_item", title="Disk full", body="On db-prod-02.", action_key="k2")
    assert "idempotent" not in kept
