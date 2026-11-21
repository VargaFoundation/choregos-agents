# SPDX-License-Identifier: Apache-2.0
"""No operation takes or returns a bank detail (core ADR 0202)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pytest

from tranibor_purchasing.profile import READS, WRITES, refuse_bank_details

from .conftest import Mcp, World

#: Invalid on purpose (no such country, a null check digit): IBAN-shaped, never an account.
SHAPED = "XX00TESTTESTTEST0000"
SHAPED_SPACED = "XX00 TEST TEST TEST 0000"
LINE = [{"description": "Paper", "quantity": "1", "unit_price": "1"}]


@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("create_supplier", {"name": "Quillfeather SAS", "country": "FR", "iban": SHAPED}),
        ("create_supplier", {"name": "Quillfeather SAS", "country": "FR", "bank_account": "0000"}),
        ("create_supplier", {"name": "Quillfeather SAS", "country": "FR", "BIC": "TESTXXXX"}),
        ("create_supplier", {"name": "Quillfeather SAS", "country": "FR", "swift_code": "TESTXXXX"}),
        ("create_supplier", {"name": "Quillfeather SAS", "country": "FR", "account_number": "0000"}),
        ("update_supplier", {"supplier_id": "SUP-0001", "fields": {"iban": SHAPED}}),
        ("update_supplier", {"supplier_id": "SUP-0001", "fields": '{"bank_name": "Nowhere Bank"}'}),
        ("update_supplier", {"supplier_id": "SUP-0001", "fields": {"bank": {"sort_code": "00-00-00"}}}),
        ("update_supplier", {"supplier_id": "SUP-0001", "fields": {"iban": SHAPED}, "bank_account": "0"}),
        ("archive_supplier", {"supplier_id": "SUP-0001", "routing_number": "000000000"}),
        ("add_contract_note", {"contract_id": "CT-0001", "text": "Wire to " + SHAPED}),
        ("add_contract_note", {"contract_id": "CT-0001", "text": "Wire to " + SHAPED_SPACED}),
        ("add_contract_note", {"contract_id": "CT-0001", "text": "ok", "payment_details": "x"}),
        ("create_supplier", {"name": "Quillfeather " + SHAPED, "country": "FR"}),
        ("create_supplier", {"name": "Quillfeather SAS", "country": "FR", "categories": [SHAPED]}),
        ("create_supplier", {"name": "Quillfeather SAS", "country": "FR", "city": SHAPED_SPACED}),
        ("create_purchase_request", {"supplier_id": "SUP-0001", "title": "Pay " + SHAPED, "lines": LINE}),
        (
            "create_purchase_request",
            {"supplier_id": "SUP-0001", "title": "T", "lines": [{**LINE[0], "bank_account": "1"}]},
        ),
        ("create_purchase_request", {"supplier_id": "SUP-0001", "title": "T", "lines": LINE, "justification": SHAPED}),
        ("cancel_purchase_request", {"request_id": "PR-0001", "reason": "refund to " + SHAPED}),
        ("search_suppliers", {"name": SHAPED}),
        ("search_suppliers", {"iban": SHAPED}),
        ("get_supplier", {"supplier_id": "SUP-0001", "include_bank_details": True}),
        ("list_contracts", {"bank_account": "1"}),
    ],
)
async def test_a_bank_shaped_argument_is_refused_and_nothing_is_written(
    operation: str, arguments: dict[str, Any], mcp: Mcp, world: World
) -> None:
    extra = {"action_key": "k-1"} if operation in WRITES else {}
    refused = await mcp.error(operation, **arguments, **extra)
    assert refused.startswith("refused:")
    assert "bank detail" in refused or "IBAN-shaped" in refused
    assert world.fake.writes == []


async def test_an_argument_a_tool_does_not_declare_is_refused_not_dropped(mcp: Mcp, world: World) -> None:
    refused = await mcp.error("create_supplier", name="Quillfeather SAS", country="FR", colour="blue", action_key="k")
    assert refused.startswith("refused:") and "takes no argument colour" in refused
    assert world.fake.writes == []


def test_the_guard_does_not_refuse_identities_that_only_look_like_one() -> None:
    for value in ("DE999000202", "GB999000101", "NL123456789B01", "HRB 990202", "FR12345678901", "+49 40 0000 0202"):
        refuse_bank_details({"vat_number": value, "text": f"Registered as {value}."})
    refuse_bank_details({"bicycle_rack": "2", "public": "yes", "cubic_metres": "3"})


@pytest.mark.parametrize("key", ["bank_holiday", "iban_of_supplier", "Swift", "payment_instructions"])
def test_the_guard_refuses_a_name_that_says_bank_detail(key: str) -> None:
    from tranibor_purchasing.errors import Refused

    with pytest.raises(Refused, match="bank detail"):
        refuse_bank_details({key: "x"})


def _walk(value: object, path: str = "") -> list[tuple[str, str]]:
    """Every (path, key-or-string) a result holds, to look for a bank detail in what a call returns."""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.append((f"{path}.{key}", str(key)))
            found.extend(_walk(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_walk(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        found.append((path, value))
    return found


@pytest.mark.parametrize("name", ["default", "duplicate-supplier", "expiring-contracts"])
async def test_no_result_of_any_operation_holds_a_bank_detail(name: str, scene: Callable[[str], World]) -> None:
    world = scene(name)
    mcp = world.mcp
    results: list[dict[str, Any]] = [
        await mcp.call("search_suppliers", include_archived=True, limit=50),
        await mcp.call("list_framework_agreements"),
        await mcp.call("list_contracts"),
    ]
    results += [await mcp.call("get_supplier", supplier_id=s["id"]) for s in results[0]["suppliers"]]
    results += [await mcp.call("get_contract", contract_id=c["id"]) for c in results[2]["contracts"]]
    results += [
        await mcp.call("create_supplier", name="Quillfeather SAS", country="FR", action_key="1"),
        await mcp.call("update_supplier", supplier_id="SUP-0002", fields={"city": "Bonn"}, action_key="2"),
        await mcp.call("archive_supplier", supplier_id="SUP-0004", action_key="3"),
        await mcp.call("add_contract_note", contract_id="CT-0001", text="Note", action_key="4"),
        await mcp.call("create_purchase_request", supplier_id="SUP-0001", title="T", lines=LINE, action_key="5"),
        await mcp.call("cancel_purchase_request", request_id="PR-0001", reason="r", action_key="6"),
    ]
    assert len(results) > 10
    key_like = re.compile(r"iban|bic|swift|bank|account|routing|sort_code|payment", re.IGNORECASE)
    for result in results:
        for path, _ in _walk(result):
            assert not key_like.search(path.rsplit(".", 1)[-1]), path
        refuse_bank_details(result, "result")


def test_the_profile_models_have_no_field_for_one() -> None:
    from tranibor_purchasing import profile

    for model in (profile.Supplier, profile.NewSupplier, profile.Contract, profile.PurchaseRequest, profile.Agreement):
        assert not [f for f in model.model_fields if re.search("iban|bank|swift|bic|account|routing", f)], model


def test_every_operation_is_a_read_or_a_write_once() -> None:
    assert set(READS).isdisjoint(WRITES) and len(READS) == 5 and len(WRITES) == 6
