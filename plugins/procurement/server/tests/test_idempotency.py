# SPDX-License-Identifier: Apache-2.0
"""Every write takes the platform's `action_key`; the same key twice writes once (ADR 0008, ADR 0096)."""

from __future__ import annotations

from typing import Any

import pytest

from tranibor_purchasing.profile import WRITES as PROFILE_WRITES

from .conftest import Mcp, World

LINE = [{"description": "Paper", "quantity": "2", "unit_price": "4.50"}]
#: One valid call of each write, without its action_key.
WRITES: dict[str, dict[str, Any]] = {
    "create_supplier": {"name": "Quillfeather Stationery SAS", "country": "FR", "registration_number": "999000808"},
    "update_supplier": {"supplier_id": "SUP-0002", "fields": {"phone": "+49 40 0000 0202"}},
    "archive_supplier": {"supplier_id": "SUP-0004", "reason": "Merged"},
    "add_contract_note": {"contract_id": "CT-0001", "text": "Reviewed."},
    "create_purchase_request": {"supplier_id": "SUP-0001", "title": "Paper", "lines": LINE},
    "cancel_purchase_request": {"request_id": "PR-0001", "reason": "Not needed"},
}


def test_the_table_covers_every_write() -> None:
    assert set(WRITES) == set(PROFILE_WRITES)


@pytest.mark.parametrize("operation", sorted(WRITES))
async def test_a_write_without_an_action_key_is_refused_and_writes_nothing(
    operation: str, mcp: Mcp, world: World
) -> None:
    refused = await mcp.error(operation, **WRITES[operation])
    assert refused.startswith("refused:") and "action_key" in refused
    assert world.fake.writes == []


@pytest.mark.parametrize("key", ["", "   ", "has a space", "x" * 201, "tab\tkey"])
async def test_a_malformed_action_key_is_refused(key: str, mcp: Mcp, world: World) -> None:
    refused = await mcp.error("add_contract_note", contract_id="CT-0001", text="n", action_key=key)
    assert refused.startswith("refused:")
    assert world.fake.writes == []


@pytest.mark.parametrize("operation", sorted(WRITES))
async def test_the_same_action_key_twice_writes_once_and_returns_the_first_result(
    operation: str, mcp: Mcp, world: World
) -> None:
    first = await mcp.call(operation, action_key="action-1", **WRITES[operation])
    assert len(world.fake.writes) == 1
    again = await mcp.call(operation, action_key="action-1", **WRITES[operation])
    assert again == first
    assert len(world.fake.writes) == 1


async def test_a_replay_returns_the_first_result_even_when_the_world_has_moved_on(mcp: Mcp, world: World) -> None:
    first = await mcp.call("archive_supplier", supplier_id="SUP-0004", action_key="a-1")
    assert first["archived"] is True
    # Another action archives nothing (it is archived); the replay of the first still says what it did.
    other = await mcp.call("archive_supplier", supplier_id="SUP-0004", action_key="a-2")
    assert other["archived"] is False
    assert (await mcp.call("archive_supplier", supplier_id="SUP-0004", action_key="a-1")) == first


async def test_two_keys_are_two_actions(mcp: Mcp, world: World) -> None:
    one = await mcp.call("add_contract_note", contract_id="CT-0001", text="Same text", action_key="k-1")
    two = await mcp.call("add_contract_note", contract_id="CT-0001", text="Same text", action_key="k-2")
    assert one["note_id"] != two["note_id"]
    assert len(world.fake.writes) == 2


async def test_a_key_reused_for_another_call_is_a_conflict_that_writes_nothing(mcp: Mcp, world: World) -> None:
    await mcp.call("add_contract_note", contract_id="CT-0001", text="First", action_key="k-1")
    other_text = await mcp.error("add_contract_note", contract_id="CT-0001", text="Second", action_key="k-1")
    other_tool = await mcp.error("archive_supplier", supplier_id="SUP-0004", action_key="k-1")
    assert other_text.startswith("conflict:") and other_tool.startswith("conflict:")
    assert len(world.fake.writes) == 1


async def test_a_failed_write_is_not_remembered(mcp: Mcp, world: World) -> None:
    refused = await mcp.error("archive_supplier", supplier_id="SUP-9999", action_key="k-1")
    assert refused.startswith("not_found:")
    ok = await mcp.call("archive_supplier", supplier_id="SUP-0004", action_key="k-1")
    assert ok["archived"] is True
    assert len(world.fake.writes) == 1


async def test_the_platform_argument_is_served_and_optional(world: World) -> None:
    """The platform validates the agent's arguments before it fills its own: `action_key` is in every write's
    schema and in none's `required`."""
    from mcp import Client

    async with Client(world.mcp.server) as client:
        served = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
    for name in PROFILE_WRITES:
        assert "action_key" in served[name]["properties"], name
        assert "action_key" not in served[name].get("required", []), name
