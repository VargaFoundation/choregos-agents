# SPDX-License-Identifier: Apache-2.0
"""The guarantees of the workflow `offer-comparison`, read from the file (PA-03).

Nothing here runs the workflow: each test parses `offer-comparison.yaml` with the core's own parser and
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
TEMPLATE = PLUGIN / "templates" / "procurement-sourcing"
WORKFLOW_FILE = TEMPLATE / "workflows" / "offer-comparison.yaml"
SCHEMA = "plugins/procurement/src/tranibor_procurement/schemas/offers_structured.schema.json"


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


def test_every_job_runs_the_procurement_image() -> None:
    jobs = {k: a for k, a in _workflow().actors.items() if a.type.value == "job"}
    assert set(jobs) == {"extractor", "calculator", "tierer"}
    for key, job in jobs.items():
        assert job.image == "procurement", key
        assert job.command[0] == "tranibor-procurement", key


def test_the_compute_step_hands_compare_every_input_it_reads() -> None:
    """`tranibor-procurement compare` reads `offers_structured` and `offers_text` (the core's
    `tranibor_procurement.compare`); 0.2.0 declared the first only, and every comparison stopped at
    `needs_human` on "input offers_text.json is missing" (found by the core's S85-10)."""
    from tranibor_procurement import compare

    assert _workflow().actors["calculator"].command[:2] == ["tranibor-procurement", "compare"]
    declared = set(_transition("t-compute").inputs)
    assert {compare.OFFERS_STRUCTURED, compare.OFFERS_TEXT} <= declared, declared


def test_the_tier_job_carries_the_committee_threshold_in_its_command() -> None:
    command = _workflow().actors["tierer"].command
    assert command[:2] == ["tranibor-procurement", "tier"]
    assert "--committee-from" in command, "the threshold is written in the workflow, never a field"
    value = command[command.index("--committee-from") + 1]
    assert float(value) > 0
    assert _transition("t-tier").by == "tierer"


def test_the_reader_and_the_analyst_keep_numbers_measured() -> None:
    assert _gate("t-read", "numbers_measured") == {
        "sources": ["offers_text"],
        "outputs": ["offers_structured"],
    }
    memo = _gate("t-memo", "numbers_measured")
    assert memo["sources"] == ["comparison", "offers_text"]
    assert memo["outputs"] == ["comparison_memo_markdown"]
    assert memo["must_quote"] == ["award_basis"], "the memo writes every number of the award basis"


def test_the_memo_keeps_its_seven_sections_in_order() -> None:
    params = _gate("t-memo", "output_sections")
    assert params["ordered"] is True
    assert params["outputs"] == {
        "comparison_memo_markdown": [
            "Summary",
            "Files read",
            "Price comparison",
            "Differences and exclusions",
            "Checks that failed",
            "Questions for the suppliers",
            "What this memo does not cover",
        ]
    }


def test_extraction_refuses_any_unreadable_file_on_measured_facts_only() -> None:
    params = _gate("t-extract", "evidence_facts")
    assert params["measured_only"] is True, "a fact an agent wrote must not clear the gate"
    assert {"files_read", "files_unreadable", "files_truncated"} <= set(params["keys"])
    assert params["max"]["files_unreadable"] == 0
    assert params["max"]["files_truncated"] == 0
    assert params["min"]["files_read"] >= 2
    assert _transition("t-extract").on_fail.to == "needs_human"


def test_the_reader_output_schema_is_the_job_images() -> None:
    schema = _gate("t-read", "output_schema")["schemas"]
    assert set(schema) == {"offers_structured"}
    path = _core_root() / SCHEMA
    assert path.is_file(), f"{path}: the core checkout does not carry the job image's schema"
    image = json.loads(path.read_text(encoding="utf-8"))
    assert _canonical(schema["offers_structured"]) == _canonical(image)


@pytest.mark.parametrize(
    ("transition_id", "actor", "group"),
    [("t-award-buyer", "buyer", "buyers"), ("t-award-committee", "committee", "purchasing-committee")],
)
def test_both_awards_are_attested_tasks_of_a_human(transition_id: str, actor: str, group: str) -> None:
    transition = _transition(transition_id)
    assert transition.to == "awarded"
    assert transition.by == actor
    deciding = _workflow().actors[actor]
    assert deciding.type.value == "human" and deciding.group == group
    assert transition.task is not None
    assert transition.task.attest and transition.task.attest.strip(), "the award is attested"
    assert transition.on_reject == "computed", "a send-back reaches the analyst with the feedback"
    assert set(transition.task.form["required"]) == {"awarded_supplier", "rationale"}


def test_only_those_two_transitions_award() -> None:
    awarding = sorted(t.id for t in _workflow().transitions if t.to == "awarded")
    assert awarding == ["t-award-buyer", "t-award-committee"]


def test_nothing_is_written_outside_the_platform() -> None:
    raw = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))
    for transition in raw["transitions"]:
        assert "action" not in transition, f"{transition['id']}: 0.1 writes nothing outside"
    assert all(getattr(t, "action", None) is None for t in _workflow().transitions)


def test_the_agents_the_workflow_names_exist_and_declare_no_memory() -> None:
    named = {a.agent for a in _workflow().actors.values() if a.type.value == "agent"}
    assert named == {"offer-reader", "sourcing-analyst"}
    shipped = yaml.safe_load((PLUGIN / "plugin.yaml").read_text(encoding="utf-8"))["contents"]["agents"]
    by_slug = {}
    for relative in shipped:
        spec = yaml.safe_load((PLUGIN / relative).read_text(encoding="utf-8"))["agent"]
        by_slug[spec["slug"]] = spec["spec"]
    assert named <= set(by_slug), "an agent the workflow names that the plugin does not ship"
    for slug in named:
        assert "memories" in by_slug[slug] and by_slug[slug]["memories"] == [], f"{slug} declares a memory"
        assert by_slug[slug]["tools"].get("web") == [], f"{slug} reaches the web"
