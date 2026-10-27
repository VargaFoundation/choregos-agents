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
