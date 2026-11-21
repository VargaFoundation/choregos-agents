# SPDX-License-Identifier: Apache-2.0
"""Each declared compensation undoes its write, rendered from the write's own result as the platform does."""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from .conftest import World
from .test_descriptor import operations

LINE = [{"description": "Paper", "quantity": "2", "unit_price": "4.50"}]


def render(template: dict[str, str], result: dict[str, Any]) -> dict[str, Any]:
    """Every argument through a sandboxed Jinja with undefined names refused: each arrives as a string."""
    env = SandboxedEnvironment(undefined=StrictUndefined)
    return {key: env.from_string(value).render(result=result) for key, value in template.items()}


async def undo(world: World, write: str, result: dict[str, Any], key: str) -> dict[str, Any]:
    compensation = operations()[write]["compensation"]
    arguments = render(compensation["arguments"], result)
    assert all(isinstance(value, str) for value in arguments.values())
    return await world.mcp.call(compensation["operation"], action_key=key, **arguments)


async def test_archive_undoes_create_supplier(world: World) -> None:
    before = await world.mcp.call("search_suppliers", include_archived=True)
    created = await world.mcp.call(
        "create_supplier",
        name="Quillfeather Stationery SAS",
        country="FR",
        registration_number="999000808",
        action_key="w",
    )
    assert (await world.mcp.call("search_suppliers", name="quillfeather"))["total"] == 1
    undone = await undo(world, "create_supplier", created, "c")
    assert (undone["status"], undone["archived"]) == ("archived", True)
    assert (await world.mcp.call("search_suppliers", name="quillfeather"))["total"] == 0
    reason = (await world.mcp.call("get_supplier", supplier_id=created["supplier_id"]))["supplier"]["archived_reason"]
    assert reason == "Creation undone."
    assert (await world.mcp.call("search_suppliers", include_archived=True))["total"] == before["total"] + 1


async def test_writing_the_previous_values_back_undoes_update_supplier(world: World) -> None:
    before = (await world.mcp.call("get_supplier", supplier_id="SUP-0001"))["supplier"]
    update = await world.mcp.call(
        "update_supplier",
        supplier_id="SUP-0001",
        fields={
            "name": "Brightwater Supplies Group",
            "email": "",  # cleared
            "phone": "+44 20 0000 9999",
            "vat_number": None,
            "postal_code": "LS2 0BB",
            "categories": ["office-supplies", "furniture"],
        },
        action_key="w",
    )
    assert update["supplier"]["email"] is None
    await undo(world, "update_supplier", update, "c")
    after = (await world.mcp.call("get_supplier", supplier_id="SUP-0001"))["supplier"]
    assert {k: v for k, v in after.items() if k != "updated_at"} == {
        k: v for k, v in before.items() if k != "updated_at"
    }


async def test_the_compensation_of_an_update_that_changed_nothing_changes_nothing(world: World) -> None:
    update = await world.mcp.call("update_supplier", supplier_id="SUP-0001", fields={"city": "Leeds"}, action_key="w")
    assert update["changed"] == [] and update["previous"] == {}
    writes = len(world.fake.writes)
    undone = await undo(world, "update_supplier", update, "c")
    assert undone["changed"] == [] and len(world.fake.writes) == writes


async def test_cancel_undoes_create_purchase_request(world: World) -> None:
    created = await world.mcp.call(
        "create_purchase_request", supplier_id="SUP-0001", title="Paper", lines=LINE, action_key="w"
    )
    assert created["status"] == "draft"
    undone = await undo(world, "create_purchase_request", created, "c")
    assert (undone["request_id"], undone["status"], undone["cancelled"]) == (created["request_id"], "cancelled", True)
    request = world.fake.requests[created["request_id"]]
    assert (request.status, request.cancel_reason) == ("cancelled", "Creation undone.")


async def test_a_replayed_compensation_undoes_once(world: World) -> None:
    created = await world.mcp.call(
        "create_purchase_request", supplier_id="SUP-0001", title="P", lines=LINE, action_key="w"
    )
    first = await undo(world, "create_purchase_request", created, "c")
    writes = len(world.fake.writes)
    assert await undo(world, "create_purchase_request", created, "c") == first
    assert len(world.fake.writes) == writes


def test_the_three_writes_without_a_compensation_say_why_in_their_description() -> None:
    for name in ("archive_supplier", "add_contract_note", "cancel_purchase_request"):
        assert "compensation" not in operations()[name]
    assert "restores it" in operations()["archive_supplier"]["description"]
    assert "no way to remove" in operations()["add_contract_note"]["description"]
