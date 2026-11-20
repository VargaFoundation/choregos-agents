# SPDX-License-Identifier: Apache-2.0
"""The `access-requests` template, played against a core checkout (CONTRIBUTING.md, "Checks").

Run from the core's root, which holds the platform's packages and its own template conformance suite:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/access-requests/tests

`tranibor plugin validate` reads a template's manifest only; these tests read what it ships the way a
project's birth and its interpreter do:

- the core's own template conformance checks (well-formed manifest, workflows with no error and no
  warning, effects that call only what the manifest announces with the arguments the `entra` operation
  takes, installable agent and skills), called on this template;
- the routing after the agent: `access` opens the grants, `bug` goes to the bug report, anything else and
  anything unsure goes to a person;
- the agent's outputs as the `output_schema` guarantee judges them, and the child fields the fan-out
  renders from them, as the child workflow's inputs judge them;
- the grant: its title, justification and params rendered as the platform renders a proposed action,
  then the effect's arguments, then the approval it waits for.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "access-requests"
MANIFEST = TEMPLATE / "manifest.yaml"
CORE = Path(os.environ.get("TRANIBOR_CORE", Path.cwd())).resolve()


def _core_conformance() -> ModuleType:
    path = CORE / "tests" / "conformance" / "templates" / "test_templates.py"
    if not path.is_file():
        raise RuntimeError(f"run from a core checkout (or set TRANIBOR_CORE): {path} not found")
    spec = importlib.util.spec_from_file_location("core_template_conformance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _core_checks() -> list[str]:
    """Every check the core plays on each of its own templates — a `test_*` taking the manifest's path
    and nothing else — whichever core this runs on: a check the core adds applies here too."""
    module = _core_conformance()
    return sorted(
        name
        for name, function in vars(module).items()
        if name.startswith("test_")
        and callable(function)
        and list(inspect.signature(function).parameters) == ["path"]
    )


def _workflow(name: str) -> Any:
    from tranibor_core.dsl import parse_workflow

    workflow, report = parse_workflow((TEMPLATE / "workflows" / f"{name}.yaml").read_text(), strict=True)
    assert report.valid and not report.warnings, report.as_dict()
    return workflow


def _gate_schemas(transition: Any) -> dict[str, Any]:
    for gate in transition.gates:
        if gate.name == "output_schema":
            return dict(gate.params["schemas"])
    raise AssertionError(f"{transition.id}: no `output_schema` guarantee")


GRANT = {
    "upn": "lea@acme.test",
    "tool": "CRM",
    "group_id": "00000000-0000-0000-0000-000000000003",
    "group_name": "app-crm-users",
    "sensitive": False,
    "reason": "I take over the northern accounts on Monday",
}
PROD = {**GRANT, "tool": "Production read", "group_id": "00000000-0000-0000-0000-000000000005",
        "group_name": "prod-read", "sensitive": True}  # fmt: skip
OUTPUTS = {
    "category": "access",
    "summary": "Lea asks for the CRM; the catalogue lists it, the directory knows her.",
    "plan": {"grants": [GRANT, PROD]},
}
#: The key a trigger gives a work item: `<PREFIX>-<trigger>.<the message id, sanitised, cut at 120>`
#: (the core's `services/triggers.item_key`); an Outlook message id is about 150 characters long.
TRIGGER_KEY = "DESK-request." + "AAMkADkxZTc0" * 10


# ── The core's own template checks, on this template ────────────────────────────────────────────────


@pytest.mark.parametrize("check", _core_checks())
def test_the_core_conformance_holds(check: str) -> None:
    getattr(_core_conformance(), check)(MANIFEST)


def test_the_core_checks_that_matter_here_are_played() -> None:
    assert {
        "test_the_manifest_is_well_formed",
        "test_the_workflows_the_default_the_routing_and_the_policy_exist",
        "test_actions_call_only_what_the_template_announces",
        "test_the_template_agents_and_skills_are_installable",
    } <= set(_core_checks())


def test_the_agent_is_complete_and_reads_one_account_at_a_time() -> None:
    from tranibor_core.agent_completeness import missing_components

    agent = yaml.safe_load((TEMPLATE / "agents" / "request-desk.yaml").read_text())
    assert missing_components(agent["spec"]) == []
    (server,) = agent["spec"]["mcp_servers"]
    (tool,) = server["tools"]
    assert (server["connector"], tool["name"]) == ("directory", "get_user"), (
        "the desk agent reads the directory and writes nothing: a write is a grant a person approves"
    )
    bound = jsonschema.Draft202012Validator(tool["arguments"])
    assert bound.is_valid({"upn": "lea@acme.test"})
    for steered in ("ceo@acme.test/manager", "lea@acme.test/memberOf", "x@y?$select=*", "lea"):
        assert not bound.is_valid({"upn": steered}), steered


def test_the_plugin_ships_this_template() -> None:
    plugin = yaml.safe_load((TEMPLATE.parents[1] / "plugin.yaml").read_text())
    assert plugin["contents"]["templates"] == ["templates/access-requests"]
    manifest = yaml.safe_load(MANIFEST.read_text())
    assert plugin["requires"]["org_connectors"] == manifest["requires"]["org_connectors"]


# ── After the agent: where each category goes ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("outputs", "expected"),
    [
        ({"category": "access"}, "settled"),
        ({"category": "ACCESS "}, "settled"),
        ({"category": "bug"}, "bug_qualified"),
        ({"category": "other"}, "to_review"),
        ({"category": "something else"}, "to_review"),
        ({}, "to_review"),
    ],
)
def test_access_opens_the_grants_a_bug_is_qualified_and_the_rest_goes_to_a_person(
    outputs: dict[str, Any], expected: str
) -> None:
    from tranibor_core.dsl.engine import WorkflowEngine

    engine = WorkflowEngine(_workflow("access-request"))
    chosen = engine.select_transition("resolved", outputs=outputs)
    assert chosen is not None and chosen.to == expected


def test_only_access_opens_grants_and_a_request_with_none_granted_goes_to_a_person() -> None:
    workflow = _workflow("access-request")
    (spawning,) = [t for t in workflow.transitions if t.spawn is not None]
    assert spawning.when == {"category": "access"}
    assert spawning.spawn.workflow == "access-grant"
    assert spawning.spawn.succeeded == ["granted"] and spawning.spawn.min_succeeded == 1
    assert spawning.on_fail is not None and spawning.on_fail.escalate_to == "to_review"
    people = {name for name, actor in workflow.actors.items() if actor.type == "human"}
    terminal_by_people = {t.to for t in workflow.transitions if t.by in people}
    assert {"handled", "handed_over"} <= terminal_by_people, "a person closes what the agent does not grant"


# ── The agent's outputs, as the guarantee judges them ──────────────────────────────────────────────


def test_the_outputs_of_an_access_request_pass_the_guarantee() -> None:
    (read,) = [t for t in _workflow("access-request").transitions if t.id == "t-read"]
    for name, schema in _gate_schemas(read).items():
        jsonschema.Draft202012Validator(schema).validate(OUTPUTS[name])


@pytest.mark.parametrize(
    "broken",
    [
        {"category": "grant"},
        {"plan": {}},
        {"plan": {"grants": [{k: v for k, v in GRANT.items() if k != "group_id"}]}},
        {"plan": {"grants": [{**GRANT, "group_id": "app-crm-users"}]}},
        {"plan": {"grants": [{**GRANT, "sensitive": "no"}]}},
        {"plan": {"grants": [{**GRANT, "upn": "lea"}]}},
        {"plan": {"grants": [{**GRANT, "upn": "lea@acme.test/memberOf"}]}},
        {"plan": {"grants": [{**GRANT, "approval": "skip"}]}},
        {"plan": {"requester": "alice@acme.test", "grants": [GRANT]}},
        {"plan": {"grants": [GRANT] * 11}},
    ],
    ids=[
        "unknown-category",
        "no-grants",
        "group-missing",
        "group-id-not-an-object-id",
        "sensitive-not-boolean",
        "upn-not-a-upn",
        "upn-with-a-path",
        "extra-key",
        "requester-from-the-agent",
        "too-many",
    ],
)
def test_outputs_the_next_step_cannot_read_are_refused(broken: dict[str, Any]) -> None:
    (read,) = [t for t in _workflow("access-request").transitions if t.id == "t-read"]
    schemas = _gate_schemas(read)
    (name,) = broken
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schemas[name]).validate(broken[name])


def _children(parent_fields: dict[str, Any], key: str) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """What the fan-out renders for each grant, as `spawn_children` renders it: (element, fields, title)."""
    from tranibor_orchestrator.activities.children import items_of, outputs_of, render

    spawning = next(t for t in _workflow("access-request").transitions if t.spawn is not None)
    documents = dict(OUTPUTS)
    rendered = []
    for rank, element in enumerate(items_of(spawning.spawn.for_each, parent_fields, documents), start=1):
        context = {
            "item": element,
            "rank": rank,
            "fields": dict(parent_fields),
            "outputs": outputs_of(documents),
            "parent": {"key": key, "title": "CRM please"},
        }
        rendered.append(
            (element, render(spawning.spawn.fields, context), render(spawning.spawn.title, context))
        )
    return rendered


def test_each_grant_becomes_a_child_whose_fields_the_grant_workflow_accepts() -> None:
    child_inputs = _workflow("access-grant").metadata.inputs
    children = _children({"requester": "lea@acme.test", "channel": "teams"}, TRIGGER_KEY)
    assert len(children) == 2
    for element, fields, title in children:
        jsonschema.Draft202012Validator(child_inputs).validate(fields)
        assert fields["sensitive"] is element["sensitive"], "a boolean stays a boolean"
        assert fields["request"] == TRIGGER_KEY, "a trigger's key, from a long message id, fits"
        assert title.startswith(element["upn"]) and element["group_id"] in title


def test_who_asked_is_the_sender_the_trigger_mapped_never_the_agents_reading() -> None:
    child_inputs = _workflow("access-grant").metadata.inputs
    (_, sent, _), _ = _children({"requester": "bob@acme.test"}, "DESK-7")
    assert sent["requester"] == "bob@acme.test"
    (_, filed, _), _ = _children({}, "DESK-8")
    assert filed["requester"] == ""
    jsonschema.Draft202012Validator(child_inputs).validate(filed)


# ── The grant: one governed action, approved by a person ───────────────────────────────────────────


def _grant_action() -> Any:
    (grant,) = [t for t in _workflow("access-grant").transitions if t.action is not None]
    return grant


@pytest.mark.parametrize("sensitive", [False, True])
def test_the_grant_is_rendered_as_the_platform_renders_a_proposed_action(sensitive: bool) -> None:
    from tranibor_api.effects import render

    action = _grant_action().action
    fields = {**GRANT, "sensitive": sensitive, "requester": "lea@acme.test", "request": "DESK-7"}
    context = {"fields": fields, "work_item": {"id": "w1", "key": "DESK-7.1", "title": "t"}}
    title = render(action.title, context)
    assert title.startswith("[sensitive] ") is sensitive
    assert title.removeprefix("[sensitive] ").startswith(f"lea@acme.test into group {GRANT['group_id']} ("), (
        "the approver reads what is written first: the sign-in name and the group's object id"
    )
    assert "(app-crm-users, CRM)" in title
    assert "requested by lea@acme.test, request DESK-7" in render(action.justification, context)
    params = render(action.params, context)
    (call,) = action.effects
    assert render(call.with_["arguments"], {"params": params, "effects": []}) == {
        "upn": "lea@acme.test",
        "group_id": GRANT["group_id"],
    }
    assert call.compensate is not None and call.compensate["with"]["operation"] == "remove_from_group"


def test_a_grant_without_requester_or_request_still_renders() -> None:
    from tranibor_api.effects import render

    action = _grant_action().action
    context = {"fields": dict(GRANT), "work_item": {"id": "w1", "key": "DESK-7.1", "title": "t"}}
    assert "requested by unknown, request none" in render(action.justification, context)


def test_the_longest_grant_title_keeps_the_group_id() -> None:
    from tranibor_api.effects import render

    longest: dict[str, Any] = {
        "upn": "a" * 100 + "@" + "b" * 19,
        "tool": "t" * 60,
        "group_id": GRANT["group_id"],
        "group_name": "g" * 100,
        "sensitive": True,
        "reason": "r",
    }
    inputs = _workflow("access-grant").metadata.inputs
    for name in ("upn", "tool", "group_name"):
        assert len(str(longest[name])) == inputs["properties"][name]["maxLength"]
    title = render(_grant_action().action.title, {"fields": longest, "work_item": {}})
    assert GRANT["group_id"] in title[:300], "the platform keeps 300 characters of a title"


def test_a_person_step_waits_30_days_and_only_then_ends_as_rejected() -> None:
    for name in ("access-request", "access-grant"):
        workflow = _workflow(name)
        people = {n for n, actor in workflow.actors.items() if actor.type == "human"}
        for transition in workflow.transitions:
            if transition.by in people and transition.on_reject:
                assert transition.timeout_hours == 720, (
                    f"{name}/{transition.id}: without it, the step is rejected after 3 x its SLA"
                )


def test_a_grant_that_failed_after_approval_is_not_proposed_again_on_its_own() -> None:
    grant = _grant_action()
    assert grant.on_fail is not None
    assert (grant.on_fail.max_attempts, grant.on_fail.escalate_to) == (1, "needs_review")


def test_the_grant_waits_for_a_re_authenticated_project_owner_and_a_refusal_writes_nothing() -> None:
    grant = _grant_action()
    approval = grant.action.approval
    assert approval is not None
    assert [(a.role, a.min) for a in approval.approvers] == [("project_owner", 1)]
    assert approval.step_up_minutes <= 5 and approval.separation_of_duties
    assert grant.on_reject == "refused"
    workflow = _workflow("access-grant")
    assert workflow.states["refused"].terminal and workflow.states["granted"].terminal
