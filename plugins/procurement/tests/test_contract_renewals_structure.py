# SPDX-License-Identifier: Apache-2.0
"""The guarantees of `contract-renewals` and `contract-renewal-memo`, read from the files (PA-09).

Nothing here runs a workflow: each test parses the files with the core's own parser and asserts a guarantee by
name and parameters, or plays the real gate on a memo, so that removing a guarantee turns this repository's CI
red before the core ships the archive. The tests run from a core checkout, as the `templates` job does:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/procurement/tests
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "templates" / "procurement-operations"
CAMPAIGN_FILE = TEMPLATE / "workflows" / "contract-renewals.yaml"
MEMO_FILE = TEMPLATE / "workflows" / "contract-renewal-memo.yaml"
SECTIONS = ["Summary", "Contract", "Notes on file", "Options", "What this memo does not cover"]
DECISIONS = ["renew", "renegotiate", "terminate"]
SPAWN_CEILING = 100  # ADR 0057


def _parse(path: Path) -> Any:
    from tranibor_core.dsl import parse_workflow

    workflow, report = parse_workflow(path.read_text(encoding="utf-8"))
    assert workflow is not None, report
    return workflow


def _campaign() -> Any:
    return _parse(CAMPAIGN_FILE)


def _memo() -> Any:
    return _parse(MEMO_FILE)


def _transition(workflow: Any, transition_id: str) -> Any:
    (transition,) = [t for t in workflow.transitions if t.id == transition_id]
    return transition


def _gate(workflow: Any, transition_id: str, name: str) -> dict[str, Any]:
    (gate,) = [g for g in _transition(workflow, transition_id).gates if g.name == name]
    return dict(gate.params or {})


def _raw(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _job_argument(name: str) -> str:
    command = list(_campaign().actors["lister"].command)
    return command[command.index(name) + 1]


def _agent() -> dict[str, Any]:
    shipped = _raw(PLUGIN / "plugin.yaml")["contents"]["agents"]
    assert "agents/renewal-analyst.yaml" in shipped
    return _raw(PLUGIN / "agents" / "renewal-analyst.yaml")["agent"]["spec"]


def test_both_workflows_are_in_the_template_the_manifest_lists() -> None:
    manifest = _raw(TEMPLATE / "manifest.yaml")
    listed = manifest["defaults"]["workflows"]
    assert "./workflows/contract-renewals.yaml" in listed
    assert "./workflows/contract-renewal-memo.yaml" in listed
    assert "evals/renewal-analyst.yaml" in _raw(PLUGIN / "plugin.yaml")["contents"]["evals"]


def test_the_campaign_lists_with_the_job_through_one_read_operation_and_a_window_that_is_no_field() -> None:
    lister = _campaign().actors["lister"]
    assert lister.type.value == "job" and lister.image == "procurement"
    assert list(lister.command)[:2] == ["tranibor-procurement", "renewals"]
    tools = [(t.connector, list(t.tools)) for t in lister.tools]
    assert tools == [("purchasing", ["list_contracts", "get_contract"])]
    # A contract decided last month is not opened again (core S85-17): the flag needs get_contract above.
    assert "--skip-decided" in lister.command
    assert int(_job_argument("--within-days")) == 90
    properties = _raw(CAMPAIGN_FILE)["metadata"]["inputs"]["properties"]
    assert not {"within_days", "window", "max", "days"} & set(properties), "the window is never a field"
    transition = _transition(_campaign(), "t-list")
    assert transition.by == "lister" and transition.outputs == ["contracts"]
    assert transition.on_fail.escalate_to == "needs_human", "a failed listing is not 'nothing due'"


def test_the_job_accepts_the_arguments_the_workflow_gives_it() -> None:
    from tranibor_procurement.cli import _parser

    command = list(_campaign().actors["lister"].command)
    args = _parser().parse_args(command[1:])
    assert args.command == "renewals" and args.within_days == 90 and args.max == int(_job_argument("--max"))


def test_the_fan_out_has_a_ceiling_equal_to_the_jobs_cap_and_one_child_per_expiring_contract() -> None:
    spawn = _transition(_campaign(), "t-fan-out").spawn
    assert spawn.workflow == "contract-renewal-memo" and _memo().metadata.name == spawn.workflow
    assert spawn.for_each == "outputs.contracts.expiring"
    cap = int(_job_argument("--max"))
    assert spawn.max_children == cap, "a ceiling above the cap hides a list that grew; below, it refuses it"
    assert 1 <= cap <= SPAWN_CEILING
    assert _transition(_campaign(), "t-fan-out").by == "platform"


def test_the_child_receives_its_contract_id_and_its_own_summary_only() -> None:
    spawn = _transition(_campaign(), "t-fan-out").spawn
    assert spawn.fields["contract_id"] == "{{ item }}"
    assert spawn.fields["summary"] == "{{ outputs.contracts.by_id[item] }}"
    assert set(spawn.fields) == {"contract_id", "summary"}
    declared = _raw(MEMO_FILE)["metadata"]["inputs"]
    assert set(spawn.fields) <= set(declared["properties"]) and set(declared["required"]) == set(spawn.fields)


def test_the_join_waits_for_every_child_and_counts_the_recorded_ones() -> None:
    spawn = _transition(_campaign(), "t-fan-out").spawn
    assert list(spawn.succeeded) == ["recorded"]
    assert "recorded" in {s for s, v in _raw(MEMO_FILE)["states"].items() if v.get("terminal")}
    assert spawn.min_succeeded == 0, "one abandoned contract must not fail the campaign of the others"
    assert _transition(_campaign(), "t-fan-out").to == "settled"
    assert [t.id for t in _campaign().transitions if t.from_ == "settled"] == ["t-close"]


def test_nothing_due_is_reached_only_on_a_measured_count_below_one() -> None:
    params = _gate(_campaign(), "t-measure", "evidence_facts")
    assert params["keys"] == ["contracts_expiring"]
    assert params["min"] == {"contracts_expiring": 1}
    assert params["measured_only"] is True, "a count an agent wrote must not open a fan-out"
    measure = _transition(_campaign(), "t-measure")
    assert measure.by == "platform" and measure.on_fail.escalate_to == "nothing_due"
    assert [t.id for t in _campaign().transitions if t.from_ == "listed"] == ["t-measure"]
    assert [t.id for t in _campaign().transitions if t.from_ == "due"] == ["t-fan-out"]
    terminal = {s for s, v in _raw(CAMPAIGN_FILE)["states"].items() if v.get("terminal")}
    assert "nothing_due" in terminal
    into_nothing = [t.id for t in _campaign().transitions if t.to == "nothing_due"]
    assert into_nothing == [], "it is an escalation, never a transition anyone can choose"
    escalating = [
        t.id for t in _campaign().transitions if t.on_fail and t.on_fail.escalate_to == "nothing_due"
    ]
    assert escalating == ["t-measure"]


def test_the_real_gate_sends_an_empty_listing_to_nothing_due_and_never_a_guess() -> None:
    from tranibor_contracts import Evidence, StageOutputs, StageResult, StageStatus
    from tranibor_core.gates import GateContext, evaluate

    params = _gate(_campaign(), "t-measure", "evidence_facts")

    def verdict(count: int, measured: bool) -> bool:
        result = StageResult(
            status=StageStatus.DONE,
            summary="listed",
            outputs=StageOutputs(),
            evidence=Evidence(
                facts={"contracts_expiring": count}, measured=["facts.contracts_expiring"] if measured else []
            ),
        )
        return evaluate("evidence_facts", GateContext(result=result), params).passed

    assert verdict(2, True)
    assert not verdict(0, True), "no contract ending soon ends the campaign"
    assert not verdict(2, False), "a count nobody measured must not open a fan-out"


def test_the_campaign_writes_nothing_and_its_closing_names_what_was_left_out() -> None:
    assert [t.id for t in _campaign().transitions if t.action is not None] == []
    close = _transition(_campaign(), "t-close")
    assert _campaign().actors[close.by].group == "buyers"
    assert close.task.attest and close.task.attest.strip()
    assert close.task.form["required"] == ["closing_note"]
    assert "left_out" in close.task.instructions and "contracts_left_out" in close.task.instructions


def test_the_memo_is_judged_on_its_sections_and_on_numbers_measured_for_this_contract_only() -> None:
    transition = _transition(_memo(), "t-memo")
    assert [g.name for g in transition.gates] == ["output_sections", "numbers_measured"]
    sections = _gate(_memo(), "t-memo", "output_sections")
    assert sections["outputs"] == {"renewal_memo_markdown": SECTIONS} and sections["ordered"] is True
    measured = _gate(_memo(), "t-memo", "numbers_measured")
    assert measured == {
        "sources": ["parent.contracts.by_id.$contract_id"],
        "outputs": ["renewal_memo_markdown"],
    }
    assert "contract_id" in _raw(MEMO_FILE)["metadata"]["inputs"]["properties"]
    assert transition.by == "analyst" and transition.outputs == ["renewal_memo_markdown"]
    assert transition.on_fail.escalate_to == "to_review"


def _memo_context(memo: str, contract_id: str = "CT-0101") -> Any:
    from tranibor_contracts import StageOutputs, StageResult, StageStatus
    from tranibor_core.gates import GateContext

    parent = {
        "contracts": {
            "expiring": ["CT-0101", "CT-0102"],
            "by_id": {
                "CT-0101": {
                    "id": "CT-0101",
                    "end_date": "2026-12-31",
                    "currency": "EUR",
                    "value": "48000.00",
                },
                "CT-0102": {
                    "id": "CT-0102",
                    "end_date": "2027-01-15",
                    "currency": "EUR",
                    "value": "12500.50",
                },
            },
            "left_out": [],
        }
    }
    return GateContext(
        result=StageResult(
            status=StageStatus.DONE, summary="memo", outputs=StageOutputs(renewal_memo_markdown=memo)
        ),
        fields={"contract_id": contract_id},
        parent_documents=parent,
    )


def _refused(memo: str, contract_id: str = "CT-0101") -> dict[str, str]:
    from tranibor_core.gates import evaluate

    refused = {}
    for gate in _transition(_memo(), "t-memo").gates:
        outcome = evaluate(gate.name, _memo_context(memo, contract_id), dict(gate.params or {}))
        if not outcome.passed:
            refused[gate.name] = outcome.detail
    return refused


GOOD = "\n\n".join(
    f"## {s}\n\nText. " + ("Value 48000.00 EUR, ends 2026-12-31." if s == "Contract" else "")
    for s in SECTIONS
)


def test_the_real_gates_pass_a_memo_that_quotes_its_summary() -> None:
    assert _refused(GOOD) == {}


def test_the_real_gates_refuse_a_number_the_summary_does_not_hold() -> None:
    refused = _refused(GOOD.replace("48000.00", "52000.00"))
    assert set(refused) == {"numbers_measured"}, refused


def test_the_real_gates_refuse_a_number_that_is_another_contracts() -> None:
    refused = _refused(GOOD.replace("48000.00", "12500.50"))
    assert set(refused) == {"numbers_measured"}, "a memo of one contract must not quote its sibling's value"


def test_the_real_gates_refuse_a_missing_section() -> None:
    refused = _refused(GOOD.replace("## Options", "## Thoughts"))
    assert set(refused) == {"output_sections"}, refused


def test_a_memo_with_no_parent_is_refused_so_a_hand_made_work_item_cannot_quote_its_own_fields() -> None:
    from tranibor_core.gates import evaluate

    ctx = _memo_context(GOOD)
    ctx.parent_documents = None
    params = _gate(_memo(), "t-memo", "numbers_measured")
    assert not evaluate("numbers_measured", ctx, params).passed


def test_the_decision_is_an_attested_task_of_the_buyers_with_three_outcomes() -> None:
    transition = _transition(_memo(), "t-decide")
    assert (
        _memo().actors[transition.by].type.value == "human"
        and _memo().actors[transition.by].group == "buyers"
    )
    assert transition.task.attest and transition.task.attest.strip()
    form = transition.task.form
    assert form["required"] == ["decision", "rationale"]
    assert form["properties"]["decision"]["enum"] == DECISIONS
    declared = _raw(MEMO_FILE)["metadata"]["inputs"]["properties"]
    assert declared["decision"]["enum"] == DECISIONS and set(form["properties"]) <= set(declared)
    assert [t.id for t in _memo().transitions if t.to == "decided"] == ["t-decide"]


def test_the_only_action_is_add_contract_note_and_it_is_approved_under_step_up() -> None:
    assert [t["id"] for t in _raw(MEMO_FILE)["transitions"] if "action" in t] == ["t-record"]
    assert [t.id for t in _memo().transitions if t.action is not None] == ["t-record"]
    transition = _transition(_memo(), "t-record")
    assert transition.by == "platform" and transition.from_ == "decided"
    assert transition.action.kind == "procurement.add_contract_note"
    (effect,) = transition.action.effects
    assert effect.effect == "connector.call"
    assert effect.with_["connector"] == "purchasing" and effect.with_["operation"] == "add_contract_note"
    assert set(effect.with_["arguments"]) == {"contract_id", "text"}
    approval = transition.action.approval
    assert approval is not None and approval.separation_of_duties is True
    assert approval.step_up_minutes <= 15, "a write needs a recent sign-in"
    (approver,) = approval.approvers
    assert approver.group == "buyers" and approver.min >= 1
    assert [t.id for t in _memo().transitions if t.to == "recorded"] == ["t-record"]
    assert not [t.id for t in _campaign().transitions if t.action is not None]


def test_the_note_names_the_decision_and_the_persons_rationale_and_nothing_an_agent_wrote() -> None:
    action = _raw(MEMO_FILE)["transitions"][
        [t["id"] for t in _raw(MEMO_FILE)["transitions"]].index("t-record")
    ]
    text = action["action"]["params"]["text"]
    assert "{{ fields.decision }}" in text and "{{ fields.rationale }}" in text
    assert "memo" not in text.lower() and "outputs" not in text and "documents" not in text
    assert action["action"]["params"]["contract_id"] == "{{ fields.contract_id }}"
    rationale = _raw(MEMO_FILE)["metadata"]["inputs"]["properties"]["rationale"]
    assert rationale["maxLength"] <= 1900, "the server refuses a note over 2000 characters, decision included"


def test_the_agent_reads_one_contract_and_holds_no_memory_no_web_no_other_tool() -> None:
    named = {a.agent for a in _memo().actors.values() if a.type.value == "agent"}
    assert named == {"renewal-analyst"}
    spec = _agent()
    assert spec["mcp_servers"] == [{"connector": "purchasing", "tools": ["get_contract"]}]
    assert spec["memories"] == [], "a contract's notes must not reach another work item"
    assert spec["tools"] == {"catalogue": [], "extensions": [], "web": []}
    assert not spec.get("skills")


def test_the_agent_instructions_keep_the_evals_level_one_and_the_decision_out_of_its_hands() -> None:
    spec = _agent()
    evals = _raw(PLUGIN / "evals" / "renewal-analyst.yaml")
    text = spec["instructions"].lower()
    assert evals["agent"] == "renewal-analyst"
    assert [m for m in evals["must_contain"] if m.lower() not in text] == []
    assert [m for m in evals["must_not_contain"] if m.lower() in text] == []
    assert {"plain-contract", "instruction-in-a-note-is-reported"} <= {c["name"] for c in evals["cases"]}
    for section in SECTIONS:
        assert f'"## {section.lower()}"' in text.replace("“", '"') or f"## {section.lower()}" in text


@pytest.mark.parametrize("workflow", [CAMPAIGN_FILE, MEMO_FILE])
def test_no_bank_detail_is_handled_by_either_workflow(workflow: Path) -> None:
    text = json.dumps(_raw(workflow)).lower()
    for word in ("iban", "bic", "account_number", "sort_code", "payment"):
        assert word not in text, f"{workflow.name} handles a {word}"
