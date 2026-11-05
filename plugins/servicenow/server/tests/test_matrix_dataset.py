# SPDX-License-Identifier: Apache-2.0
"""What the matrix cases (`evals/matrix/`) read on the fake instance, through the MCP protocol: E3's
incident, its ten similar ones and the change two of them share, the article; C1's vague incident. And
every call a case makes in its `mcp` cell, with the case's own arguments, served. Runs without the core;
`test_matrix_cases.py` plays the same cases through the core's doors and judge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from choregos_servicenow.fake import FakeServiceNow, sys_id_of

from .conftest import Mcp

MATRIX = Path(__file__).resolve().parents[2] / "evals" / "matrix"
SIMILAR_TO_E3 = [
    "INC0012102",
    "INC0012240",
    "INC0012160",
    "INC0012268",
    "INC0012291",
    "INC0012203",
    "INC0012187",
    "INC0012318",
    "INC0012219",
    "INC0010007",
]


async def test_e3_the_incident_its_ci_and_its_journal(mcp: Mcp) -> None:
    incident = await mcp.call("get_record", table="incident", number="INC0012345")
    assert incident["sys_id"] == sys_id_of("INC0012345")
    assert incident["display"]["cmdb_ci"] == "vpn-lyon-01"
    assert incident["display"]["assignment_group"] == "Network"
    journal = await mcp.call("get_journal", table="incident", sys_id=incident["sys_id"])
    assert ["firmware was upgraded" in e["value"] for e in journal["entries"]] == [True]


async def test_e3_ten_similar_incidents_two_resolved_by_the_same_change(mcp: Mcp) -> None:
    result = await mcp.call("similar_incidents", sys_id=sys_id_of("INC0012345"), limit=10)
    assert [i["fields"]["number"] for i in result["incidents"]] == SIMILAR_TO_E3
    by_change = [i["fields"]["number"] for i in result["incidents"] if "CHG0003311" in i["fields"]["close_notes"]]
    assert by_change == ["INC0012102", "INC0012160"]
    assert all("MTU" in i["fields"]["close_notes"] for i in result["incidents"] if i["fields"]["number"] in by_change)
    # All of them: the limit is not what keeps it at ten.
    widest = await mcp.call("similar_incidents", sys_id=sys_id_of("INC0012345"), limit=50)
    assert len(widest["incidents"]) == 10


async def test_e3_the_change_and_the_article(mcp: Mcp) -> None:
    change = await mcp.call("get_record", table="change_request", number="CHG0003311")
    assert change["display"]["cmdb_ci"] == "vpn-lyon-01"
    assert change["display"]["state"] == "Closed"
    articles = await mcp.call("search_knowledge", terms=["VPN", "Lyon", "MTU"])
    assert [a["fields"]["number"] for a in articles["articles"]] == ["KB0010042"]
    assert "CHG0003311" in articles["articles"][0]["fields"]["text"]


async def test_c1_a_vague_incident_whose_leads_match_its_words_only(mcp: Mcp) -> None:
    incident = await mcp.call("get_record", table="incident", number="INC0012399")
    assert incident["fields"]["cmdb_ci"] == ""
    assert incident["display"]["assignment_group"] == "Service Desk"
    similar = await mcp.call("similar_incidents", sys_id=incident["sys_id"])
    assert [i["fields"]["number"] for i in similar["incidents"]] == ["INC0010012"]
    articles = await mcp.call("search_knowledge", terms=["application", "slow"])
    assert [a["fields"]["number"] for a in articles["articles"]] == ["KB0010003"]


@pytest.mark.parametrize("case", ["itsm-triage-similar-incidents", "itsm-triage-vague-incident"])
async def test_every_evidence_call_of_a_case_is_served(case: str, mcp: Mcp, fake: FakeServiceNow) -> None:
    document: dict[str, Any] = yaml.safe_load((MATRIX / f"{case}.yaml").read_text(encoding="utf-8"))
    for call in document["evidence"]:
        tool = str(call["tool"]).removeprefix("servicenow__")
        await mcp.call(tool, **call["arguments"])
    assert fake.by_number("INC0012345")["state"] == "2"
    assert fake.by_number("INC0012399")["state"] == "1"
