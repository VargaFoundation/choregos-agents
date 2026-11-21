# SPDX-License-Identifier: Apache-2.0
"""What the matrix cases (`evals/matrix/`) read on the fake, through the MCP protocol: the agreements in force
on each case's date, the contract of the renewal case. And every call a case makes, with the case's own
arguments, served. Runs without the core; `test_matrix_cases.py` plays the same cases through the core's doors
and judge."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from .conftest import Mcp, World

MATRIX = Path(__file__).resolve().parents[2] / "evals" / "matrix"
CASES = {
    "request-agreement-serves-need": "default",
    "request-no-agreement-serves-need": "default",
    "renewal-memo-reads-the-contract": "expiring-contracts",
}


async def test_on_the_date_of_the_first_case_an_office_supplies_agreement_serves_the_need(mcp: Mcp) -> None:
    found = await mcp.call("list_framework_agreements", valid_on="2026-12-10")
    assert [(a["id"], a["category"]) for a in found["agreements"]] == [
        ("FA-0003", "facilities"),
        ("FA-0001", "office-supplies"),
    ]
    # The logistics agreement ended on 2026-10-31: it is not in force, and it is not returned.
    assert "FA-0002" not in str(found)
    assert found["next_cursor"] is None
    assert str(found["agreements"][1]["valid_to"]) == "2027-12-31"


async def test_on_the_date_of_the_second_case_no_agreement_covers_a_translation(mcp: Mcp) -> None:
    found = await mcp.call("list_framework_agreements", valid_on="2027-02-15")
    assert [(a["id"], a["category"]) for a in found["agreements"]] == [
        ("FA-0003", "facilities"),
        ("FA-0001", "office-supplies"),
    ]
    assert not [a for a in found["agreements"] if "translation" in f"{a['category']} {a['title']} {a['terms_summary']}"]


async def test_the_renewal_case_s_contract_is_one_of_the_four_the_scene_adds(
    scene: Callable[[str], World],
) -> None:
    world = scene("expiring-contracts")
    contract = (await world.mcp.call("get_contract", contract_id="CT-0004"))["contract"]
    assert (contract["supplier_id"], contract["category"], contract["end_date"]) == (
        "SUP-0004",
        "facilities",
        "2026-12-05",
    )
    assert (contract["value"], contract["currency"], contract["notes"]) == ("36000.00", "EUR", [])
    ending = await world.mcp.call("list_contracts", ending_before="2027-03-01")
    assert "CT-0004" in [c["id"] for c in ending["contracts"]]


def test_an_agreement_a_case_cites_stays_below_ten() -> None:
    """The cases cite agreements 1 to 9 only, so they also hold on a core without S85-16 (`FA-0012` read as 12)."""
    from tranibor_purchasing.backends.fake import FakePurchasing

    numbers = [int(a.removeprefix("FA-")) for a in FakePurchasing("default").agreements]
    assert max(numbers) < 10


@pytest.mark.parametrize("case", sorted(CASES))
async def test_every_evidence_call_of_a_case_is_served(case: str, scene: Callable[[str], World]) -> None:
    document: dict[str, Any] = yaml.safe_load((MATRIX / f"{case}.yaml").read_text(encoding="utf-8"))
    assert document["connectors"]["purchasing"]["scene"] == CASES[case]
    world = scene(CASES[case])
    for call in document["evidence"]:
        tool = str(call["tool"]).removeprefix("purchasing__")
        await world.mcp.call(tool, **call["arguments"])
    assert world.fake.writes == []
