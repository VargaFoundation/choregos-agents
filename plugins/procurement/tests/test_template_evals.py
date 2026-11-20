# SPDX-License-Identifier: Apache-2.0
"""The template's eval cases, played through the gates the workflow declares (PA-03).

Each case of `templates/procurement-sourcing/evals/cases.yaml` is an output an agent could write. It is
scored by the real gates of the core (`tranibor_core.gates.evaluate`) with the parameters read from
`offer-comparison.yaml`: `t-read` for the reader's `offers_structured`, `t-memo` for the analyst's memo.
The context is built as the core's own template evals build it: a `StageResult` carrying the output, and
the measured documents the gates read as sources. A loosened guarantee turns a `refuse` case green, and
this suite red.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "procurement-sourcing"
CASES = yaml.safe_load((TEMPLATE / "evals" / "cases.yaml").read_text(encoding="utf-8"))
READER = {"transition": "t-read", "output": "offers_structured", "sources": ["offers_text"]}
MEMO = {
    "transition": "t-memo",
    "output": "comparison_memo_markdown",
    "sources": ["comparison", "award_basis", "offers_text"],
}


def _gates(transition_id: str) -> list[tuple[str, dict[str, Any]]]:
    from tranibor_core.dsl import parse_workflow

    text = (TEMPLATE / "workflows" / "offer-comparison.yaml").read_text(encoding="utf-8")
    workflow, report = parse_workflow(text)
    assert workflow is not None, report
    (transition,) = [t for t in workflow.transitions if t.id == transition_id]
    return [(g.name, dict(g.params or {})) for g in transition.gates]


def _score(stage: dict[str, Any], value: Any) -> dict[str, str]:
    """The gates of the stage's transition that refuse `value`, with their detail."""
    from tranibor_contracts import StageOutputs, StageResult, StageStatus
    from tranibor_core.gates import GateContext, evaluate

    ctx = GateContext(
        result=StageResult(
            status=StageStatus.DONE, summary="eval", outputs=StageOutputs(**{stage["output"]: value})
        ),
        documents={k: CASES["documents"][k] for k in stage["sources"]},
    )
    refused = {}
    for name, params in _gates(stage["transition"]):
        outcome = evaluate(name, ctx, params)
        if not outcome.passed:
            refused[name] = outcome.detail
    return refused


def _play(stage: dict[str, Any], case: dict[str, Any]) -> None:
    refused = _score(stage, case["memo" if stage is MEMO else stage["output"]])
    if case["expect"] == "accept":
        assert not refused, refused
        return
    assert refused, f"{case['id']} should be refused: {case['why']}"
    assert case["fails"] in refused, f"{case['id']} must fail {case['fails']}, failed {sorted(refused)}"
    assert case.get("reason_contains", "") in refused[case["fails"]], refused


@pytest.mark.parametrize("case", CASES["offers_structured"], ids=lambda c: c["id"])
def test_the_reader_cases(case: dict[str, Any]) -> None:
    _play(READER, case)


@pytest.mark.parametrize("case", CASES["memo"], ids=lambda c: c["id"])
def test_the_memo_cases(case: dict[str, Any]) -> None:
    _play(MEMO, case)


def test_the_gates_played_are_the_ones_the_workflow_declares() -> None:
    assert [n for n, _ in _gates("t-read")] == ["output_schema", "numbers_measured"]
    assert [n for n, _ in _gates("t-memo")] == ["output_sections", "numbers_measured"]


def test_every_guarantee_has_a_case_it_refuses() -> None:
    """A guarantee without a refused case is not evaluated: each gate must catch at least one case."""
    for key, transition in (("offers_structured", "t-read"), ("memo", "t-memo")):
        cases = CASES[key]
        assert {c["expect"] for c in cases} == {"accept", "refuse"}
        caught = {c["fails"] for c in cases if c["expect"] == "refuse"}
        assert caught == {n for n, _ in _gates(transition)}, key


def test_the_memo_cases_hold_the_failures_the_instructions_forbid() -> None:
    ids = {c["id"] for c in CASES["memo"]}
    assert {"section-missing", "decimal-comma-amount", "award-basis-mismatch-left-unwritten"} <= ids
    analyst = (TEMPLATE.parents[1] / "agents" / "sourcing-analyst.yaml").read_text(encoding="utf-8")
    assert "no thousands separator" in analyst.lower()
