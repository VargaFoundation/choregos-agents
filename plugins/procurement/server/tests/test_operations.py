# SPDX-License-Identifier: Apache-2.0
"""Each of the eleven operations through MCP on the fake, in its scenes."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from .conftest import Mcp, World

ACME = "09990101"  # the registration number of SUP-0001 in the fake


async def test_search_suppliers_filters_are_typed_and_combine(mcp: Mcp) -> None:
    by_name = await mcp.call("search_suppliers", name="print")
    assert [s["id"] for s in by_name["suppliers"]] == ["SUP-0003"]
    # A registration number matches whatever its spaces, dots and dashes.
    assert [s["id"] for s in (await mcp.call("search_suppliers", registration_number="999-000.303"))["suppliers"]] == [
        "SUP-0003"
    ]
    assert [s["id"] for s in (await mcp.call("search_suppliers", country="de"))["suppliers"]] == ["SUP-0002"]
    nothing = await mcp.call("search_suppliers", country="DE", name="print")
    assert (nothing["suppliers"], nothing["total"], nothing["next_cursor"]) == ([], 0, None)


async def test_search_suppliers_hides_the_archived_unless_asked_and_pages(mcp: Mcp) -> None:
    default = await mcp.call("search_suppliers")
    assert default["total"] == 4
    assert "SUP-0005" not in [s["id"] for s in default["suppliers"]]
    everyone = await mcp.call("search_suppliers", include_archived=True, limit=3)
    assert (everyone["total"], len(everyone["suppliers"])) == (5, 3)
    assert everyone["next_cursor"] is not None
    last = await mcp.call("search_suppliers", include_archived=True, limit=3, cursor=everyone["next_cursor"])
    assert (len(last["suppliers"]), last["next_cursor"]) == (2, None)
    assert [s["id"] for s in everyone["suppliers"] + last["suppliers"]] == [
        "SUP-0004",
        "SUP-0001",
        "SUP-0002",
        "SUP-0005",
        "SUP-0003",
    ]


@pytest.mark.parametrize(
    "arguments", [{"limit": 0}, {"limit": 101}, {"cursor": "not-a-cursor"}, {"cursor": "b9"}, {"country": "FRA"}]
)
async def test_search_suppliers_refuses_a_malformed_filter(mcp: Mcp, arguments: dict[str, object]) -> None:
    assert (await mcp.error("search_suppliers", **arguments)).startswith("refused:")


async def test_get_supplier(mcp: Mcp) -> None:
    supplier = (await mcp.call("get_supplier", supplier_id="SUP-0001"))["supplier"]
    assert supplier["name"] == "Brightwater Office Supplies Ltd"
    assert supplier["categories"] == ["office-supplies"]
    assert supplier["status"] == "active"
    assert (await mcp.error("get_supplier", supplier_id="SUP-9999")).startswith("not_found:")
    assert (await mcp.error("get_supplier", supplier_id="../etc")).startswith("refused:")


async def test_list_framework_agreements_by_category_and_date(mcp: Mcp) -> None:
    every = await mcp.call("list_framework_agreements")
    assert [a["id"] for a in every["agreements"]] == ["FA-0003", "FA-0002", "FA-0001"]
    assert [a["id"] for a in (await mcp.call("list_framework_agreements", category="Logistics"))["agreements"]] == [
        "FA-0002"
    ]
    # Valid on a date: both ends included, no end date means open-ended.
    on = await mcp.call("list_framework_agreements", valid_on="2026-11-20")
    assert [a["id"] for a in on["agreements"]] == ["FA-0003", "FA-0001"]
    last_day = await mcp.call("list_framework_agreements", category="logistics", valid_on="2026-10-31")
    assert [a["id"] for a in last_day["agreements"]] == ["FA-0002"]
    assert (await mcp.error("list_framework_agreements", valid_on="20/11/2026")).startswith("refused:")


async def test_list_contracts_ending_before_a_date_and_by_supplier(mcp: Mcp) -> None:
    ending = await mcp.call("list_contracts", ending_before="2027-01-01")
    # Earliest end first; the end date itself is not "before".
    assert [(c["id"], c["end_date"]) for c in ending["contracts"]] == [
        ("CT-0003", "2026-09-30"),
        ("CT-0002", "2026-12-31"),
    ]
    assert (await mcp.call("list_contracts", ending_before="2026-12-31"))["total"] == 1
    assert [c["id"] for c in (await mcp.call("list_contracts", supplier_id="SUP-0001"))["contracts"]] == ["CT-0001"]
    paged = await mcp.call("list_contracts", limit=2)
    assert (paged["total"], len(paged["contracts"])) == (3, 2)
    rest = await mcp.call("list_contracts", limit=2, cursor=paged["next_cursor"])
    assert ([c["id"] for c in rest["contracts"]], rest["next_cursor"]) == (["CT-0003"], None)
    assert "notes" not in paged["contracts"][0]
    assert paged["contracts"][0]["note_count"] == 0


async def test_get_contract_with_its_notes(mcp: Mcp) -> None:
    contract = (await mcp.call("get_contract", contract_id="CT-0001"))["contract"]
    assert (contract["supplier_id"], contract["end_date"], contract["notes"]) == ("SUP-0001", "2027-06-30", [])
    assert (await mcp.error("get_contract", contract_id="CT-9999")).startswith("not_found:")


async def test_create_supplier_without_payment_data_and_a_duplicate_is_refused(mcp: Mcp) -> None:
    created = await mcp.call(
        "create_supplier",
        name="Quillfeather Stationery SAS",
        country="fr",
        registration_number="999 000 808",
        email="hello@quillfeather.example",
        categories=["Office-Supplies", "office-supplies"],
        action_key="k-1",
    )
    assert created["created"] is True
    supplier = created["supplier"]
    assert (supplier["country"], supplier["categories"], supplier["status"]) == ("FR", ["office-supplies"], "active")
    assert (await mcp.call("get_supplier", supplier_id=created["supplier_id"]))["supplier"] == supplier
    again = await mcp.error(
        "create_supplier", name="Another name", country="FR", registration_number="999000808", action_key="k-2"
    )
    assert again.startswith("conflict: duplicate_supplier") and created["supplier_id"] in again
    # Without a registration number, the same name in the same country is the duplicate.
    same_name = await mcp.error(
        "create_supplier", name="QUILLFEATHER stationery, S.A.S.", country="FR", action_key="k-3"
    )
    assert same_name.startswith("conflict: duplicate_supplier")
    # An archived supplier's identity is free again.
    ok = await mcp.call(
        "create_supplier", name="Marrowdale Catering Co", country="IE", registration_number="999505", action_key="k-4"
    )
    assert ok["created"] is True


@pytest.mark.parametrize(
    "arguments",
    [
        {"name": " ", "country": "FR"},
        {"name": "X", "country": "France"},
        {"name": "X", "country": "FR", "email": "not an address"},
        {"name": "X", "country": "FR", "categories": ["Office Supplies"]},
    ],
)
async def test_create_supplier_refuses_a_malformed_record(mcp: Mcp, world: World, arguments: dict[str, object]) -> None:
    assert (await mcp.error("create_supplier", action_key="k", **arguments)).startswith("refused:")
    assert world.fake.writes == []


async def test_update_supplier_returns_what_changed_and_what_it_was(mcp: Mcp) -> None:
    done = await mcp.call(
        "update_supplier",
        supplier_id="SUP-0002",
        fields={"phone": "+49 40 0000 0202", "city": "Hamburg", "email": "", "categories": ["logistics", "freight"]},
        action_key="u-1",
    )
    # `city` was already Hamburg: not a change.
    assert done["changed"] == ["categories", "email", "phone"]
    assert done["previous"] == {"categories": ["logistics"], "email": "orders@kestrel-logistik.example", "phone": None}
    assert (done["supplier"]["email"], done["supplier"]["phone"]) == (None, "+49 40 0000 0202")
    json_text = await mcp.call("update_supplier", supplier_id="SUP-0002", fields='{"city": "Bremen"}', action_key="u-2")
    assert json_text["previous"] == {"city": "Hamburg"}


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"status": "archived"}, "cannot change status"),
        ({"id": "SUP-0009"}, "cannot change id"),
        ({"name": ""}, "name cannot be cleared"),
        ("not json", "fields must be an object"),
        ("[1]", "fields"),
    ],
)
async def test_update_supplier_refuses_what_is_not_a_field(mcp: Mcp, fields: object, message: str) -> None:
    assert message in await mcp.error("update_supplier", supplier_id="SUP-0001", fields=fields, action_key="u")


async def test_update_supplier_refuses_an_archived_supplier_and_a_colliding_identity(mcp: Mcp) -> None:
    archived = await mcp.error("update_supplier", supplier_id="SUP-0005", fields={"city": "Dublin"}, action_key="u-1")
    assert archived.startswith("conflict:")
    collision = await mcp.error(
        "update_supplier",
        supplier_id="SUP-0002",
        fields={"registration_number": ACME, "country": "GB"},
        action_key="u-2",
    )
    assert collision.startswith("conflict: duplicate_supplier") and "SUP-0001" in collision
    assert (await mcp.error("update_supplier", supplier_id="SUP-9999", fields={}, action_key="u-3")).startswith(
        "not_found:"
    )


async def test_archive_supplier_takes_it_out_of_searches_and_of_new_requests(mcp: Mcp) -> None:
    done = await mcp.call(
        "archive_supplier", supplier_id="SUP-0004", reason="Merged into another supplier", action_key="a-1"
    )
    assert (done["status"], done["previous_status"], done["archived"]) == ("archived", "active", True)
    assert "SUP-0004" not in [s["id"] for s in (await mcp.call("search_suppliers"))["suppliers"]]
    supplier = (await mcp.call("get_supplier", supplier_id="SUP-0004"))["supplier"]
    assert supplier["archived_reason"] == "Merged into another supplier"
    refused = await mcp.error(
        "create_purchase_request",
        supplier_id="SUP-0004",
        title="Cleaning",
        lines=[{"description": "Hours", "quantity": "10", "unit_price": "30"}],
        action_key="p-1",
    )
    assert refused.startswith("conflict:")
    # Archiving what is archived changes nothing (a new action, so a new key).
    again = await mcp.call("archive_supplier", supplier_id="SUP-0004", action_key="a-2")
    assert (again["archived"], again["previous_status"]) == (False, "archived")


async def test_add_contract_note(mcp: Mcp) -> None:
    done = await mcp.call(
        "add_contract_note", contract_id="CT-0002", text="Renewal discussed on 20 November.", action_key="n-1"
    )
    assert done["contract_id"] == "CT-0002"
    notes = (await mcp.call("get_contract", contract_id="CT-0002"))["contract"]["notes"]
    assert [(n["id"], n["text"]) for n in notes] == [(done["note_id"], "Renewal discussed on 20 November.")]
    assert (await mcp.error("add_contract_note", contract_id="CT-0002", text="  ", action_key="n-2")).startswith(
        "refused:"
    )
    assert (await mcp.error("add_contract_note", contract_id="CT-0002", text="x" * 2001, action_key="n-3")).startswith(
        "refused:"
    )
    assert (await mcp.error("add_contract_note", contract_id="CT-9999", text="x", action_key="n-4")).startswith(
        "not_found:"
    )


async def test_create_purchase_request_computes_its_totals_and_is_only_a_draft(mcp: Mcp) -> None:
    done = await mcp.call(
        "create_purchase_request",
        supplier_id="SUP-0001",
        contract_id="CT-0001",
        title="Folders and pens",
        lines=[
            {"description": "Folders", "quantity": "3", "unit_price": "19.99", "unit": "box"},
            {"description": "Pens", "quantity": "0.5", "unit_price": "10"},
        ],
        needed_by="2026-12-10",
        action_key="p-1",
    )
    assert (done["status"], done["total"], done["currency"]) == ("draft", "64.97", "EUR")
    assert [line["line_total"] for line in done["request"]["lines"]] == ["59.97", "5.00"]
    assert done["request"]["needed_by"] == "2026-12-10"
    assert done["request_id"] == "PR-0002"


@pytest.mark.parametrize(
    "arguments",
    [
        {"lines": []},
        {"lines": [{"description": "x", "quantity": "0", "unit_price": "1"}]},
        {"lines": [{"description": "x", "quantity": "1", "unit_price": "-1"}]},
        {"lines": [{"description": "x", "quantity": "1", "unit_price": "1,5"}]},
        {"lines": [{"description": "x", "quantity": "1"}]},
        {"lines": [{"description": "x", "quantity": "1", "unit_price": "1", "discount": "5"}]},
        {"lines": [{"description": "x", "quantity": "1", "unit_price": "1"}], "currency": "euro"},
        {"lines": [{"description": "x", "quantity": "1", "unit_price": "1"}], "needed_by": "tomorrow"},
    ],
)
async def test_create_purchase_request_refuses_a_malformed_request(
    mcp: Mcp, world: World, arguments: dict[str, object]
) -> None:
    refused = await mcp.error("create_purchase_request", supplier_id="SUP-0001", title="T", action_key="p", **arguments)
    assert refused.startswith("refused:")
    assert world.fake.writes == []


async def test_a_purchase_request_names_a_contract_of_its_own_supplier(mcp: Mcp) -> None:
    line = [{"description": "x", "quantity": "1", "unit_price": "1"}]
    wrong = await mcp.error(
        "create_purchase_request",
        supplier_id="SUP-0002",
        contract_id="CT-0001",
        title="T",
        lines=line,
        action_key="p-1",
    )
    assert wrong.startswith("conflict:") and "CT-0001" in wrong
    missing = await mcp.error(
        "create_purchase_request", supplier_id="SUP-9999", title="T", lines=line, action_key="p-2"
    )
    assert missing.startswith("not_found:")


async def test_cancel_purchase_request(mcp: Mcp) -> None:
    done = await mcp.call("cancel_purchase_request", request_id="PR-0001", reason="No longer needed", action_key="c-1")
    assert (done["status"], done["previous_status"], done["cancelled"]) == ("cancelled", "draft", True)
    again = await mcp.call("cancel_purchase_request", request_id="PR-0001", reason="No longer needed", action_key="c-2")
    assert (again["cancelled"], again["previous_status"]) == (False, "cancelled")
    assert (await mcp.error("cancel_purchase_request", request_id="PR-9999", reason="r", action_key="c-3")).startswith(
        "not_found:"
    )
    assert (await mcp.error("cancel_purchase_request", request_id="PR-0001", reason="", action_key="c-4")).startswith(
        "refused:"
    )


async def test_scene_duplicate_supplier(scene: Callable[[str], World]) -> None:
    world = scene("duplicate-supplier")
    found = await world.mcp.call("search_suppliers", registration_number=ACME)
    assert [s["id"] for s in found["suppliers"]] == ["SUP-0007", "SUP-0001"]
    refused = await world.mcp.error(
        "create_supplier", name="Brightwater", country="GB", registration_number=ACME, action_key="k"
    )
    assert refused.startswith("conflict: duplicate_supplier") and "SUP-0007, SUP-0001" in refused
    assert world.fake.writes == []
    # The underscore spelling names the same scene.
    assert scene("duplicate_supplier").fake.scene == "duplicate-supplier"


async def test_scene_expiring_contracts(scene: Callable[[str], World]) -> None:
    world = scene("expiring-contracts")
    soon = await world.mcp.call("list_contracts", ending_before="2027-02-20")
    assert [(c["id"], c["end_date"]) for c in soon["contracts"]] == [
        ("CT-0003", "2026-09-30"),
        ("CT-0004", "2026-12-05"),
        ("CT-0002", "2026-12-31"),
        ("CT-0005", "2027-01-31"),
        ("CT-0006", "2027-02-14"),
    ]
    everything = await world.mcp.call("list_contracts", limit=100)
    assert everything["total"] == 7
    # A contract that never ends is not "ending before" any date.
    assert "CT-0007" not in [
        c["id"] for c in (await world.mcp.call("list_contracts", ending_before="9999-12-31"))["contracts"]
    ]


async def test_the_shapes_the_cores_job_commands_expect(scene: Callable[[str], World]) -> None:
    """S85-12 reads `{suppliers, next_cursor}` and `{contracts, next_cursor}`, pages with `limit` and an opaque
    `cursor`, and needs `id` and `name` of a supplier, `id`, `supplier_id`, `title` and `end_date` of a contract."""
    world = scene("expiring-contracts")
    suppliers = await world.mcp.call("search_suppliers", name="a", country="NL", limit=1)
    assert set(suppliers) >= {"suppliers", "next_cursor"}
    assert {"id", "name", "registration_number", "country"} <= set(suppliers["suppliers"][0])
    contracts = await world.mcp.call("list_contracts", ending_before="2027-03-01", supplier_id="SUP-0001", limit=1)
    assert [c["id"] for c in contracts["contracts"]] == ["CT-0005"]
    assert {"id", "supplier_id", "title", "end_date"} <= set(contracts["contracts"][0])
    assert contracts["next_cursor"] is None
    # Walking the cursor visits every contract once, then `next_cursor` is null.
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = await world.mcp.call("list_contracts", limit=2, **({"cursor": cursor} if cursor else {}))
        seen += [c["id"] for c in page["contracts"]]
        cursor = page["next_cursor"]
        assert cursor is None or isinstance(cursor, str)
        if cursor is None:
            break
    assert seen == [f"CT-000{n}" for n in range(1, 8)]
