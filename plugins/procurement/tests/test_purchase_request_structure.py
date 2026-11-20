# SPDX-License-Identifier: Apache-2.0
"""The guarantees of the workflow `purchase-request` and its agent `request-qualifier`, read from the
files (PA-08).

Nothing here runs the workflow: each test parses `purchase-request.yaml` with the core's own parser and
asserts
a guarantee by name and parameters, or plays the core's real gates on an output an agent could write, so that
removing a guarantee turns this repository's CI red before the core ships the archive. The tests run from a
core checkout, as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/procurement/tests
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "procurement-operations"
WORKFLOW_FILE = TEMPLATE / "workflows" / "purchase-request.yaml"
ROUTES = ["catalogue", "framework_agreement", "three_quotes", "tender"]


def _raw() -> Any:
    return yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))


def _workflow() -> Any:
    from tranibor_core.dsl import parse_workflow

    workflow, report = parse_workflow(WORKFLOW_FILE.read_text(encoding="utf-8"))
    assert workflow is not None, report
    return workflow


def _transition(transition_id: str) -> Any:
    (transition,) = [t for t in _workflow().transitions if t.id == transition_id]
    return transition


def _gates(transition_id: str) -> list[str]:
    return [g.name for g in _transition(transition_id).gates]


def _gate(transition_id: str, name: str) -> dict[str, Any]:
    (gate,) = [g for g in _transition(transition_id).gates if g.name == name]
    return dict(gate.params or {})


def _agent() -> Any:
    return yaml.safe_load((PLUGIN / "agents" / "request-qualifier.yaml").read_text(encoding="utf-8"))


def _schema() -> dict[str, Any]:
    return dict(_gate("t-qualify", "output_schema")["schemas"]["qualification"])


def _proposal(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "category": "office-supplies",
        "route": "framework_agreement",
        "agreement_id": "FA-0001",
        "rationale": "The office supplies framework is in force on the date needed.",
    }
    return {**value, **changes}


def _score(outputs: dict[str, Any], *, fields: dict[str, Any], calls: list[str]) -> dict[str, str]:
    """The gates of `t-qualify` that refuse `outputs`, with their detail: the core's own gates, as the engine
    plays them."""
    from tranibor_contracts import StageOutputs, StageResult, StageStatus
    from tranibor_core.gates import GateContext, evaluate

    context = GateContext(
        result=StageResult(status=StageStatus.DONE, summary="eval", outputs=StageOutputs(**outputs)),
        fields=fields,
        tool_calls=calls,
    )
    refused = {}
    for gate in _transition("t-qualify").gates:
        outcome = evaluate(gate.name, context, dict(gate.params or {}))
        if not outcome.passed:
            refused[gate.name] = outcome.detail
    return refused


LOOKED = ["purchasing__list_framework_agreements"]
TYPED = {"need": "Printer toner", "estimated_amount": 3200, "currency": "EUR", "needed_by": "2026-12-10"}


def _outputs(**changes: Any) -> dict[str, Any]:
    proposal = _proposal(**changes)
    return {
        "qualification": proposal,
        "route": proposal["route"],
        "brief_markdown": "## Request\nPrinter toner, 3200 EUR, needed by 2026-12-10.\n",
    }


# --- the file ---------------------------------------------------------------------------------------------


def test_the_workflow_validates_and_is_the_templates_second() -> None:
    assert _workflow().metadata.name == "purchase-request"
    manifest = yaml.safe_load((TEMPLATE / "manifest.yaml").read_text(encoding="utf-8"))
    assert "./workflows/purchase-request.yaml" in manifest["defaults"]["workflows"]
    assert "./workflows/supplier-onboarding.yaml" in manifest["defaults"]["workflows"]


def test_the_workflow_opens_nothing_in_the_purchasing_system() -> None:
    """Launching a consultation is the buyer's next gesture: no governed action, no job, no write."""
    raw = _raw()
    assert [t["id"] for t in raw["transitions"] if "action" in t] == []
    assert not [name for name, actor in raw["actors"].items() if actor["type"] == "job"]
    lines = WORKFLOW_FILE.read_text(encoding="utf-8").splitlines()
    code = [ln for ln in lines if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "action:" in ln or "effects:" in ln or "connector.call" in ln]


def test_the_request_fields_and_what_is_required() -> None:
    properties = _raw()["metadata"]["inputs"]["properties"]
    assert {"need", "estimated_amount", "currency", "needed_by"} <= set(properties)
    # The amount is a number a person types, and it is NOT required: a request that states none is the
    # agent's question, never a default.
    assert properties["estimated_amount"]["type"] == "number"
    assert _raw()["metadata"]["inputs"]["required"] == ["need"]


def test_the_agent_must_always_have_listed_the_framework_agreements() -> None:
    """A gate on a transition always applies, so the call is required whatever the route; it is read from the
    ledger as the connector's tool is named there, `<connector>__<operation>`."""
    assert _gate("t-qualify", "tool_called") == {"tools": LOOKED}
    refused = _score(_outputs(), fields=TYPED, calls=[])
    assert list(refused) == ["tool_called"], refused
    assert _score(_outputs(), fields=TYPED, calls=["purchasing__search_suppliers"]).keys() == {"tool_called"}
    assert _score(_outputs(), fields=TYPED, calls=LOOKED) == {}


def test_the_route_is_one_of_four_and_the_gate_agrees_with_the_schema() -> None:
    assert _gate("t-qualify", "outputs_in") == {"values": {"route": ROUTES}}
    assert _schema()["properties"]["route"]["enum"] == ROUTES
    refused = _score(_outputs(route="negotiation", agreement_id=None), fields=TYPED, calls=LOOKED)
    assert {"output_schema", "outputs_in"} <= set(refused), refused


@pytest.mark.parametrize(
    ("changes", "valid"),
    [
        ({}, True),
        ({"route": "catalogue", "agreement_id": None}, True),
        ({"route": "tender", "agreement_id": None}, True),
        ({"agreement_id": None}, False),  # a framework agreement route names its agreement
        ({"route": "three_quotes"}, False),  # any other route names none
        ({"route": "negotiation", "agreement_id": None}, False),
        ({"category": "Office Supplies"}, False),
        ({"rationale": ""}, False),
    ],
)
def test_the_qualification_schema(changes: dict[str, Any], valid: bool) -> None:
    from jsonschema import Draft202012Validator

    errors = list(Draft202012Validator(_schema()).iter_errors(_proposal(**changes)))
    assert (not errors) is valid, errors
    assert list(Draft202012Validator(_schema()).iter_errors({**_proposal(), "extra": 1})), "no other key"


def test_the_amount_the_agent_writes_is_the_one_the_person_typed() -> None:
    params = _gate("t-qualify", "numbers_measured")
    assert params["sources"] == ["fields"]
    assert sorted(params["outputs"]) == ["brief_markdown", "qualification"]
    assert _score(_outputs(), fields=TYPED, calls=LOOKED) == {}
    # An agreement numbered 10 or more is a reference, not an unmeasured amount (core S84-16).
    assert params["identifiers"] == "skip"
    twelfth = _outputs(agreement_id="FA-0012", rationale="Agreement FA-0012 covers office supplies.")
    assert "numbers_measured" not in _score(twelfth, fields=TYPED, calls=LOOKED)
    # An amount the person did not type (a rounding, a guess) is refused, in the brief ...
    guessed = {**_outputs(), "brief_markdown": "Printer toner, 3500 EUR."}
    assert "numbers_measured" in _score(guessed, fields=TYPED, calls=LOOKED)
    # ... and in the rationale.
    in_rationale = _outputs(rationale="About 5000 EUR, above the quotes threshold.")
    assert "numbers_measured" in _score(in_rationale, fields=TYPED, calls=LOOKED)


def test_a_request_with_no_amount_cannot_be_given_one() -> None:
    """The route depends on the amount: with none typed, any amount the agent writes is measured nowhere."""
    no_amount = {"need": "Standing desks", "needed_by": "2027-01-20"}
    assert "numbers_measured" in _score(_outputs(), fields=no_amount, calls=LOOKED)
    assert _score(_outputs(), fields=TYPED, calls=LOOKED) == {}


def test_the_gates_are_the_four_and_in_order() -> None:
    assert _gates("t-qualify") == ["output_schema", "outputs_in", "numbers_measured", "tool_called"]
    assert _transition("t-qualify").outputs == ["qualification", "route", "brief_markdown"]


def test_a_failed_proposal_is_retried_then_goes_to_a_person_and_a_question_parks() -> None:
    on_fail = _raw()["transitions"][0]["on_fail"]
    assert on_fail["escalate_to"] == "needs_human" and on_fail["max_attempts"] >= 1
    defaults = _raw()["defaults"]
    assert defaults["from_any_agent_state"]["on_question"] == "needs_human"
    assert defaults["needs_human"]["on_answer"] == "resume"


def test_the_buyer_reviews_attested_and_the_terminal_states_name_the_route() -> None:
    raw = _raw()
    states = raw["states"]
    assert {s for s, v in states.items() if v.get("terminal")} >= {f"routed_{r}" for r in ROUTES}
    tasks = {t["to"]: t for t in raw["transitions"] if t["from"] == "awaiting_review"}
    for route in ROUTES:
        task = tasks[f"routed_{route}"]
        assert task["by"] == "buyer"
        assert task["task"]["attest"].strip(), f"{route}: the buyer attests"
        assert task["task"]["form"]["required"] == ["review_note"]
        assert task["on_reject"] == "received", f"{route}: a buyer can send the proposal back"
    # The buyer may take any route, not only the proposed one: four tasks leave the review, plus a decline.
    assert len(tasks) == len(ROUTES) + 1 and "declined" in tasks
    assert raw["actors"]["buyer"]["type"] == "human"
    # Nothing leaves the review without a person: the agent's transition ends at a wait.
    assert states["awaiting_review"]["kind"] == "wait"
    assert [t["to"] for t in raw["transitions"] if t["from"] == "qualified"] == ["awaiting_review"]


# --- the agent --------------------------------------------------------------------------------------------


def test_the_agent_reads_the_two_tools_the_policy_and_nothing_else() -> None:
    spec = _agent()["agent"]["spec"]
    assert spec["mcp_servers"] == [
        {"connector": "purchasing", "tools": ["list_framework_agreements", "search_suppliers"]}
    ]
    assert spec["knowledge"] == [{"slug": "purchasing-policy"}]
    assert spec["tools"] == {"catalogue": [], "extensions": [], "web": []}
    assert spec["memories"] == []
    ledger_name = "purchasing__list_framework_agreements"
    assert ledger_name == f"{spec['mcp_servers'][0]['connector']}__{spec['mcp_servers'][0]['tools'][0]}"


def test_the_agent_names_the_outputs_the_workflow_declares() -> None:
    agent = _agent()
    assert agent["catalogue"]["outputs"] == _transition("t-qualify").outputs
    assert _raw()["actors"]["qualifier"]["agent"] == agent["agent"]["slug"] == "request-qualifier"
    assert _raw()["actors"]["qualifier"]["role"] == agent["catalogue"]["role"]


def test_the_template_ships_the_empty_policy_base_the_agent_reads() -> None:
    manifest = yaml.safe_load((TEMPLATE / "manifest.yaml").read_text(encoding="utf-8"))
    (base,) = manifest["defaults"]["knowledge"]
    assert base["slug"] == _agent()["agent"]["spec"]["knowledge"][0]["slug"] == "purchasing-policy"
    assert "sources" not in base, "the organisation fills it with its own rules"
    contents = yaml.safe_load((PLUGIN / "plugin.yaml").read_text(encoding="utf-8"))["contents"]
    assert "agents/request-qualifier.yaml" in contents["agents"]
    assert "evals/request-qualifier.yaml" in contents["evals"]


def test_the_agent_evals_hold_against_the_instructions_and_cover_the_cases() -> None:
    spec = _agent()["agent"]["spec"]
    evals = yaml.safe_load((PLUGIN / "evals" / "request-qualifier.yaml").read_text(encoding="utf-8"))
    text = spec["instructions"].lower()
    assert evals["agent"] == "request-qualifier"
    assert [m for m in evals["must_contain"] if m.lower() not in text] == []
    assert [m for m in evals["must_not_contain"] if m.lower() in text] == []
    cases = {c["name"]: c for c in evals["cases"]}
    assert {
        "agreement-serves-the-need",
        "no-agreement-serves-the-need",
        "amount-not-stated-the-agent-asks",
        "instruction-in-the-request-is-reported-not-followed",
    } <= set(cases)
    asking = cases["amount-not-stated-the-agent-asks"]
    # The agent asks: the run waits for the answer, and the request it was given states no amount.
    assert asking["expect_status"] == "blocked"
    assert "estimated_amount" not in asking["input"]["fields"]
    assert all("estimated_amount" in c["input"]["fields"] for n, c in cases.items() if c is not asking)
    assert "FA-0001" in "".join(cases["agreement-serves-the-need"]["must_contain"])


def test_the_instructions_say_how_to_ask_and_to_always_look() -> None:
    text = " ".join(_agent()["agent"]["spec"]["instructions"].split())
    assert "call `ask_human`" in text
    assert "Always call `list_framework_agreements`" in text
    assert "Never guess, estimate or infer an amount" in text
