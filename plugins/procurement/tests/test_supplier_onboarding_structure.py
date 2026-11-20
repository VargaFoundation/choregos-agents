# SPDX-License-Identifier: Apache-2.0
"""The guarantees of the workflow `supplier-onboarding`, read from the file (PA-07).

Nothing here runs the workflow: each test parses `supplier-onboarding.yaml` with the core's own parser and
asserts a guarantee by name and parameters, so that removing the guarantee turns this repository's CI red
before the core ships the archive. The tests run from a core checkout, as the `templates` job does:

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
WORKFLOW_FILE = TEMPLATE / "workflows" / "supplier-onboarding.yaml"
SCHEMA = "plugins/procurement/src/tranibor_procurement/schemas/supplier_file.schema.json"
FACTS = ["documents_missing", "documents_expired", "identity_mismatch", "duplicates_found"]


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


def _gate(transition_id: str, name: str) -> dict[str, Any]:
    (gate,) = [g for g in _transition(transition_id).gates if g.name == name]
    return dict(gate.params or {})


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _core_root() -> Path:
    """The core checkout the tests run from: the one `tranibor_core` is installed from (CI: `core/`)."""
    import tranibor_core

    return Path(tranibor_core.__file__).resolve().parents[4]


def _agents() -> dict[str, dict[str, Any]]:
    shipped = yaml.safe_load((PLUGIN / "plugin.yaml").read_text(encoding="utf-8"))["contents"]["agents"]
    by_slug = {}
    for relative in shipped:
        spec = yaml.safe_load((PLUGIN / relative).read_text(encoding="utf-8"))["agent"]
        by_slug[spec["slug"]] = spec["spec"]
    return by_slug


def test_the_only_action_is_the_creation_and_it_writes_nothing_else() -> None:
    acting = [t["id"] for t in _raw()["transitions"] if "action" in t]
    assert acting == ["t-create"], "a second action would be a second governed write"
    assert [t.id for t in _workflow().transitions if t.action is not None] == ["t-create"]
    transition = _transition("t-create")
    assert transition.by == "platform"
    assert transition.action.kind == "procurement.create_supplier"
    (effect,) = transition.action.effects
    assert effect.effect == "connector.call"
    assert effect.with_["connector"] == "purchasing"
    assert effect.with_["operation"] == "create_supplier"


def test_the_creation_takes_the_identity_and_the_category_and_never_a_bank_detail() -> None:
    action = _raw()["transitions"][[t["id"] for t in _raw()["transitions"]].index("t-create")]["action"]
    arguments = action["effects"][0]["with"]["arguments"]
    assert set(arguments) == {"name", "registration_number", "country", "categories"}
    assert set(action["params"]) == {"name", "registration_number", "country", "category"}
    declared = _raw()["metadata"]["inputs"]["properties"]
    assert set(declared) == {
        "legal_name",
        "registration_number",
        "country",
        "category",
        "review_note",
        "decline_reason",
    }
    for text in (json.dumps(action), json.dumps(declared), json.dumps(_raw()["actors"])):
        for word in ("iban", "bic", "bank", "account_number", "sort_code", "payment"):
            assert word not in text.lower(), f"the workflow handles a {word}"


def test_the_creation_is_approved_by_a_group_and_the_reviewer_cannot_be_the_only_approver() -> None:
    approval = _transition("t-create").action.approval
    assert approval is not None
    assert approval.separation_of_duties is True
    assert approval.step_up_minutes <= 15, "a creation needs a recent sign-in"
    (approver,) = approval.approvers
    reviewer = _workflow().actors["reviewer"]
    assert approver.group == reviewer.group == "supplier-data"
    assert approver.min >= 2, "the reviewer belongs to the group: one approver could be the reviewer alone"


def test_the_creation_follows_a_review_by_the_group_and_nothing_else_reaches_it() -> None:
    into_approved = [t for t in _workflow().transitions if t.to == "approved"]
    assert [t.id for t in into_approved] == ["t-review"]
    assert [t.id for t in _workflow().transitions if t.to == "created"] == ["t-create"]
    assert _transition("t-create").from_ == "approved"


def test_the_review_is_an_attested_task_of_the_group() -> None:
    transition = _transition("t-review")
    reviewer = _workflow().actors[transition.by]
    assert reviewer.type.value == "human" and reviewer.group == "supplier-data"
    assert transition.task is not None
    assert transition.task.attest and transition.task.attest.strip(), "the review is attested"
    assert transition.task.form["required"] == ["review_note"]
    assert _transition("t-decline").to == "declined"


def test_the_check_job_measures_the_four_findings_and_refuses_any_on_measured_facts() -> None:
    params = _gate("t-check", "evidence_facts")
    assert params["measured_only"] is True, "a fact an agent wrote must not clear the gate"
    assert params["keys"] == FACTS
    assert params["max"] == dict.fromkeys(FACTS, 0)
    assert "min" not in params, "a minimum would let a finding hide behind a count"
    transition = _transition("t-check")
    assert transition.by == "inspector"
    assert transition.on_fail.to == "needs_human"
    assert transition.outputs == ["supplier_check"], "the findings reach the person who answers"
    assert transition.inputs == ["supplier_file"]


@pytest.mark.parametrize("finding", FACTS)
def test_the_real_gate_refuses_each_finding_and_passes_a_clean_file(finding: str) -> None:
    from tranibor_contracts import Evidence, StageOutputs, StageResult, StageStatus
    from tranibor_core.gates import GateContext, evaluate

    params = _gate("t-check", "evidence_facts")

    def verdict(facts: dict[str, int], measured: list[str]) -> bool:
        result = StageResult(
            status=StageStatus.DONE,
            summary="check",
            outputs=StageOutputs(),
            evidence=Evidence(facts=facts, measured=measured),
        )
        return evaluate("evidence_facts", GateContext(result=result), params).passed

    clean = dict.fromkeys(FACTS, 0)
    everything = [f"facts.{k}" for k in FACTS]
    assert verdict(clean, everything)
    assert not verdict({**clean, finding: 1}, everything), f"{finding} = 1 must stop the file"
    assert not verdict(clean, []), "facts nobody measured must not clear the gate"


def test_the_check_job_carries_the_required_kinds_the_date_and_the_duplicate_search() -> None:
    inspector = _workflow().actors["inspector"]
    command = list(inspector.command)
    assert command[:2] == ["tranibor-procurement", "supplier-check"]
    assert inspector.image == "procurement"
    required = command[command.index("--required") + 1 : command.index("--as-of")]
    assert len(required) >= 1 and all(not k.startswith("--") for k in required), "the kinds are in the file"
    assert command[command.index("--as-of") + 1] == "today", "a fixed date would age the check"
    assert "--duplicates" in command
    assert [(t.connector, list(t.tools)) for t in inspector.tools] == [("purchasing", ["search_suppliers"])]
    assert "required" not in {*_raw()["metadata"]["inputs"]["properties"]}, "the kinds are never a field"


def test_the_jobs_run_the_procurement_image() -> None:
    jobs = {k: a for k, a in _workflow().actors.items() if a.type.value == "job"}
    assert set(jobs) == {"extractor", "inspector"}
    for key, job in jobs.items():
        assert job.image == "procurement", key
        assert job.command[0] == "tranibor-procurement", key


def test_extraction_refuses_any_unreadable_file_on_measured_facts_only() -> None:
    params = _gate("t-extract", "evidence_facts")
    assert params["measured_only"] is True
    assert params["max"]["files_unreadable"] == 0
    assert params["max"]["files_truncated"] == 0
    assert params["min"]["files_read"] >= 1
    assert _transition("t-extract").on_fail.to == "needs_human"


def test_the_agent_output_schema_is_the_job_images() -> None:
    schemas = _gate("t-file", "output_schema")["schemas"]
    assert set(schemas) == {"supplier_file"}
    path = _core_root() / SCHEMA
    assert path.is_file(), f"{path}: the core checkout does not carry the job image's schema"
    image = json.loads(path.read_text(encoding="utf-8"))
    assert _canonical(schemas["supplier_file"]) == _canonical(image)
    assert _transition("t-file").by == "checker"


def test_the_agent_the_workflow_names_exists_and_holds_no_memory_no_tool_no_web() -> None:
    named = {a.agent for a in _workflow().actors.values() if a.type.value == "agent"}
    assert named == {"supplier-file-checker"}
    spec = _agents()["supplier-file-checker"]
    assert spec["memories"] == [], "a supplier's file must not reach another work item"
    assert spec["tools"] == {"catalogue": [], "extensions": [], "web": []}
    assert not spec.get("skills")


def test_the_agent_instructions_keep_the_evals_level_one_and_the_measure_out_of_its_hands() -> None:
    spec = _agents()["supplier-file-checker"]
    evals = yaml.safe_load((PLUGIN / "evals" / "supplier-file-checker.yaml").read_text(encoding="utf-8"))
    text = spec["instructions"].lower()
    assert evals["agent"] == "supplier-file-checker"
    assert [m for m in evals["must_contain"] if m.lower() not in text] == []
    assert [m for m in evals["must_not_contain"] if m.lower() in text] == []
    names = [c["name"] for c in evals["cases"]]
    assert {
        "complete-file",
        "expired-certificate-is-dated-not-judged",
        "registration-number-copied-as-written",
        "injection-and-bank-detail-not-copied",
    } <= set(names)


def test_the_template_needs_the_purchasing_connector_and_the_plugin_ships_it() -> None:
    manifest = yaml.safe_load((TEMPLATE / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["requires"]["org_connectors"] == {"purchasing": "mcp"}
    assert manifest["defaults"]["workflows"] == ["./workflows/supplier-onboarding.yaml"]
    contents = yaml.safe_load((PLUGIN / "plugin.yaml").read_text(encoding="utf-8"))["contents"]
    assert "templates/procurement-operations" in contents["templates"]
    assert "agents/supplier-file-checker.yaml" in contents["agents"]
    assert "evals/supplier-file-checker.yaml" in contents["evals"]
    other = PLUGIN / "templates" / "procurement-sourcing" / "manifest.yaml"
    sourcing = yaml.safe_load(other.read_text(encoding="utf-8"))
    assert "org_connectors" not in sourcing["requires"], "offers must compare without purchasing"
