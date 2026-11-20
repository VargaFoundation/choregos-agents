# SPDX-License-Identifier: Apache-2.0
"""The plugin's two templates, played against a core checkout (CONTRIBUTING.md, "Checks").

Run from the core's root, which holds the platform's packages and its own template conformance suite:

    cd <core checkout> && uv sync --frozen
    uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/access-requests/tests

`access-requests` takes its requests from a trigger (internal tracker); `access-requests-m365` from the
`microsoft-365-desk` plugin's server (a shared mailbox, a Teams channel) and answers in the thread.
`tranibor plugin validate` reads a template's manifest only; these tests read what each ships the way a
project's birth and its interpreter do:

- the core's own template conformance checks, called on each template;
- the routing after the agent: `access` opens the grants, `bug` the bug report, anything else and anything
  unsure goes to a person;
- the agent's outputs as the `output_schema` guarantee judges them, and the child fields the fan-out
  renders from them, as the child workflows' inputs judge them;
- the grant and the bug report rendered as the platform renders a proposed action, with the approval
  each waits for: the tool's approvers group, never the person it grants nor the requester;
- the replies (Microsoft 365): only fixed text, identifiers or a person's answer — never the agent's text;
- the two templates do not drift apart: same agent, skills, outputs and grant.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import jsonschema
import pytest
import yaml

PLUGINS = Path(__file__).resolve().parents[2]
BASE, M365 = "access-requests", "access-requests-m365"
#: Each template's folder: the Microsoft 365 one is the `microsoft-365-desk` plugin's, since its tracker is
#: that plugin's server (a template's plugin tracker is its own plugin's).
FOLDER = {
    BASE: PLUGINS / "access-requests" / "templates" / BASE,
    M365: PLUGINS / "microsoft-365-desk" / "templates" / M365,
}
CORE = Path(os.environ.get("TRANIBOR_CORE", Path.cwd())).resolve()
both = pytest.mark.parametrize("template", [BASE, M365])


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


#: The core's own templates only take a tracker type the adapters register; a template's tracker that is
#: a plugin's server (`plugin:<plugin>/<server>`) is checked by the plugin installer instead, and here.
PLUGIN_TRACKER_CHECK = "test_the_declared_connectors_exist"


def _manifest(template: str) -> dict[str, Any]:
    return dict(yaml.safe_load((FOLDER[template] / "manifest.yaml").read_text()))


def _workflow(template: str, name: str) -> Any:
    from tranibor_core.dsl import parse_workflow

    text = (FOLDER[template] / "workflows" / f"{name}.yaml").read_text()
    workflow, report = parse_workflow(text, strict=True)
    assert report.valid and not report.warnings, report.as_dict()
    return workflow


def _transition(template: str, workflow: str, transition: str) -> Any:
    (found,) = [t for t in _workflow(template, workflow).transitions if t.id == transition]
    return found


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
    "approver_group": "crm",
    "reason": "I take over the northern accounts on Monday",
}
PROD = {**GRANT, "tool": "Production read", "group_id": "00000000-0000-0000-0000-000000000005",
        "group_name": "prod-read", "sensitive": True, "approver_group": "platform-leads"}  # fmt: skip
BUG = {"project": "expenses", "title": "Export fails with a 500", "report": "**Symptom**: a 500 on export."}
OUTPUTS = {
    "category": "access",
    "summary": "Lea asks for the CRM; the catalogue lists it, the directory knows her.",
    "plan": {"grants": [GRANT, PROD], "bugs": []},
}
#: The key a trigger gives a work item: `<PREFIX>-<trigger>.<the message id, sanitised, cut at 120>`
#: (the core's `services/triggers.item_key`); an Outlook message id is about 150 characters long.
TRIGGER_KEY = "DESK-request." + "AAMkADkxZTc0" * 10
#: Who sent it, as each template's project knows it: a trigger's field, or the desk tracker's reporter.
SENDER: dict[str, dict[str, Any]] = {
    BASE: {"requester": "bob@acme.test"},
    M365: {"tracker": {"reporter": {"account_id": None, "display_name": "bob@acme.test", "email": None}}},
}


# ── The core's own template checks, on each template ───────────────────────────────────────────────


@both
@pytest.mark.parametrize("check", _core_checks())
def test_the_core_conformance_holds(template: str, check: str) -> None:
    if check == PLUGIN_TRACKER_CHECK and str(
        _manifest(template)["requires"]["connectors"]["tracker"]
    ).startswith("plugin:"):
        pytest.skip("a plugin's server as tracker: see test_the_m365_template_runs_on_the_desk_server")
    getattr(_core_conformance(), check)(FOLDER[template] / "manifest.yaml")


def test_the_core_checks_that_matter_here_are_played() -> None:
    assert {
        "test_the_manifest_is_well_formed",
        "test_the_workflows_the_default_the_routing_and_the_policy_exist",
        "test_actions_call_only_what_the_template_announces",
        "test_the_template_agents_and_skills_are_installable",
    } <= set(_core_checks())


def test_the_m365_template_runs_on_the_desk_server() -> None:
    manifest = _manifest(M365)
    assert manifest["requires"]["connectors"] == {"tracker": "plugin:microsoft-365-desk/desk"}
    assert manifest["requires"]["org_connectors"] == {"directory": "identity", "desk": "mcp"}
    assert _manifest(BASE)["requires"]["connectors"] == {"tracker": "internal"}


def test_each_plugin_ships_its_template_and_announces_its_connectors() -> None:
    base = yaml.safe_load((PLUGINS / "access-requests" / "plugin.yaml").read_text())
    assert base["contents"]["templates"] == [f"templates/{BASE}"]
    assert base["requires"]["org_connectors"] == {"directory": "identity"}
    desk = yaml.safe_load((PLUGINS / "microsoft-365-desk" / "plugin.yaml").read_text())
    assert f"templates/{M365}" in desk["contents"]["templates"]
    assert desk["requires"]["org_connectors"] == {"directory": "identity"}, (
        "the desk connector is the plugin's own server, installed with it; the directory is the organisation's"
    )


@both
def test_the_agent_is_complete_and_reads_one_account_at_a_time(template: str) -> None:
    from tranibor_core.agent_completeness import missing_components

    agent = yaml.safe_load((FOLDER[template] / "agents" / "request-desk.yaml").read_text())
    assert missing_components(agent["spec"]) == []
    (server,) = agent["spec"]["mcp_servers"]
    (tool,) = server["tools"]
    assert (server["connector"], tool["name"]) == ("directory", "get_user"), (
        "the desk agent reads the directory and writes nothing: a write is an action a person approves"
    )
    bound = jsonschema.Draft202012Validator(tool["arguments"])
    assert bound.is_valid({"upn": "lea@acme.test"})
    for steered in ("ceo@acme.test/manager", "lea@acme.test/memberOf", "x@y?$select=*", "lea"):
        assert not bound.is_valid({"upn": steered}), steered


# ── The two templates do not drift apart ───────────────────────────────────────────────────────────


def _tree(template: str, folder: str) -> dict[str, str]:
    root = FOLDER[template] / folder
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_agent_and_the_skills_are_the_same_in_both_templates() -> None:
    assert _tree(BASE, "agents") == _tree(M365, "agents")
    assert _tree(BASE, "skills") == _tree(M365, "skills")


def test_the_agents_outputs_and_the_grants_approval_are_the_same_in_both_templates() -> None:
    assert _gate_schemas(_transition(BASE, "access-request", "t-read")) == _gate_schemas(
        _transition(M365, "access-request", "t-read")
    )
    for workflow, transition in (("access-grant", "t-grant"), ("bug-report", "t-file")):
        base, m365 = _transition(BASE, workflow, transition), _transition(M365, workflow, transition)
        assert base.action.model_dump() == m365.action.model_dump(), (workflow, "the governed write")
    for workflow in ("access-grant", "bug-report"):
        assert _workflow(BASE, workflow).metadata.inputs == _workflow(M365, workflow).metadata.inputs


# ── After the agent: where each category goes ──────────────────────────────────────────────────────


@both
@pytest.mark.parametrize(
    ("outputs", "expected"),
    [
        ({"category": "access"}, "settled"),
        ({"category": "ACCESS "}, "settled"),
        ({"category": "bug"}, "bug_filed"),
        ({"category": "other"}, "to_review"),
        ({"category": "something else"}, "to_review"),
        ({}, "to_review"),
    ],
)
def test_access_opens_the_grants_a_bug_its_report_and_the_rest_goes_to_a_person(
    template: str, outputs: dict[str, Any], expected: str
) -> None:
    from tranibor_core.dsl.engine import WorkflowEngine

    chosen = WorkflowEngine(_workflow(template, "access-request")).select_transition(
        "resolved", outputs=outputs
    )
    assert chosen is not None and chosen.to == expected


@both
def test_each_fan_out_settles_on_a_success_or_goes_to_a_person(template: str) -> None:
    spawns = {t.spawn.workflow: t for t in _workflow(template, "access-request").transitions if t.spawn}
    assert set(spawns) == {"access-grant", "bug-report"}
    assert spawns["access-grant"].when == {"category": "access"}
    assert spawns["bug-report"].when == {"category": "bug"}
    for name, succeeded in (("access-grant", ["granted"]), ("bug-report", ["filed"])):
        spawn = spawns[name]
        assert spawn.spawn.succeeded == succeeded and spawn.spawn.min_succeeded == 1
        assert spawn.on_fail is not None and spawn.on_fail.escalate_to == "to_review"


# ── The agent's outputs, as the guarantee judges them ──────────────────────────────────────────────


@both
def test_the_outputs_of_an_access_request_and_of_a_bug_pass_the_guarantee(template: str) -> None:
    schemas = _gate_schemas(_transition(template, "access-request", "t-read"))
    for outputs in (OUTPUTS, {**OUTPUTS, "category": "bug", "plan": {"grants": [], "bugs": [BUG]}}):
        for name, schema in schemas.items():
            jsonschema.Draft202012Validator(schema).validate(outputs[name])


@pytest.mark.parametrize(
    "broken",
    [
        {"category": "grant"},
        {"plan": {"grants": []}},
        {"plan": {"grants": [{k: v for k, v in GRANT.items() if k != "group_id"}], "bugs": []}},
        {"plan": {"grants": [{**GRANT, "group_id": "app-crm-users"}], "bugs": []}},
        {"plan": {"grants": [{**GRANT, "sensitive": "no"}], "bugs": []}},
        {"plan": {"grants": [{**GRANT, "upn": "lea@acme.test/memberOf"}], "bugs": []}},
        {"plan": {"grants": [{k: v for k, v in GRANT.items() if k != "approver_group"}], "bugs": []}},
        {"plan": {"grants": [{**GRANT, "approver_group": "{{ fields.x }}"}], "bugs": []}},
        {"plan": {"grants": [{**GRANT, "approval": "skip"}], "bugs": []}},
        {"plan": {"grants": [GRANT] * 11, "bugs": []}},
        {"plan": {"grants": [], "bugs": [BUG, BUG]}},
        {"plan": {"grants": [], "bugs": [{**BUG, "project": "Expenses Tool"}]}},
        {"plan": {"grants": [], "bugs": [{**BUG, "report": "x" * 3501}]}},
    ],
    ids=[
        "unknown-category",
        "no-bugs-list",
        "group-missing",
        "group-id-not-an-object-id",
        "sensitive-not-boolean",
        "upn-with-a-path",
        "no-approvers",
        "approvers-a-template",
        "extra-key",
        "too-many-grants",
        "two-bugs",
        "project-not-a-slug",
        "report-too-long",
    ],
)
def test_outputs_the_next_step_cannot_read_are_refused(broken: dict[str, Any]) -> None:
    schemas = _gate_schemas(_transition(BASE, "access-request", "t-read"))
    (name,) = broken
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schemas[name]).validate(broken[name])


def _children(
    template: str, workflow: str, outputs: dict[str, Any], parent_fields: dict[str, Any], key: str
) -> Any:
    """What the fan-out renders for each element, as `spawn_children` renders it: (element, fields, title)."""
    from tranibor_orchestrator.activities.children import items_of, outputs_of, render

    spawning = next(
        t
        for t in _workflow(template, "access-request").transitions
        if t.spawn and t.spawn.workflow == workflow
    )
    rendered = []
    for rank, element in enumerate(items_of(spawning.spawn.for_each, parent_fields, dict(outputs)), start=1):
        context = {
            "item": element,
            "rank": rank,
            "fields": dict(parent_fields),
            "outputs": outputs_of(dict(outputs)),
            "parent": {"key": key, "title": "CRM please"},
        }
        rendered.append(
            (element, render(spawning.spawn.fields, context), render(spawning.spawn.title, context))
        )
    return rendered


@both
def test_each_grant_becomes_a_child_whose_fields_the_grant_workflow_accepts(template: str) -> None:
    child_inputs = _workflow(template, "access-grant").metadata.inputs
    children = _children(template, "access-grant", OUTPUTS, SENDER[template], TRIGGER_KEY)
    assert len(children) == 2
    for element, fields, title in children:
        jsonschema.Draft202012Validator(child_inputs).validate(fields)
        assert fields["sensitive"] is element["sensitive"], "a boolean stays a boolean"
        assert fields["approver_group"] == element["approver_group"]
        assert fields["request"] == TRIGGER_KEY, "a trigger's key, from a long message id, fits"
        assert fields["requester"] == "bob@acme.test", "who asked is the sender, not the agent's reading"
        assert title.startswith(element["upn"]) and element["group_id"] in title


@both
def test_a_bug_becomes_one_child_whose_fields_the_bug_workflow_accepts(template: str) -> None:
    outputs = {**OUTPUTS, "category": "bug", "plan": {"grants": [], "bugs": [BUG]}}
    ((_, fields, title),) = _children(template, "bug-report", outputs, SENDER[template], "DESK-9")
    jsonschema.Draft202012Validator(_workflow(template, "bug-report").metadata.inputs).validate(fields)
    assert (fields["project"], fields["requester"], fields["request"]) == (
        "expenses",
        "bob@acme.test",
        "DESK-9",
    )
    assert BUG["title"] in title


@both
def test_with_no_known_sender_nobody_is_named_the_requester(template: str) -> None:
    ((_, fields, _), _) = _children(template, "access-grant", OUTPUTS, {}, "DESK-8")
    assert fields["requester"] == ""
    jsonschema.Draft202012Validator(_workflow(template, "access-grant").metadata.inputs).validate(fields)


# ── The governed writes: rendered as proposed, and who may decide them ─────────────────────────────


def _render(template: str, value: Any, fields: dict[str, Any], key: str = "DESK-7.1") -> Any:
    from tranibor_api.effects import render

    return render(value, {"fields": fields, "work_item": {"id": "w1", "key": key, "title": "t"}})


def _proposed(template: str, workflow: str, transition: str, fields: dict[str, Any]) -> dict[str, Any]:
    """The action as the platform stores it when proposed: rendered title, params and approval."""
    from tranibor_api.services.actions import rendered_approval

    action = _transition(template, workflow, transition).action
    context = {"fields": fields, "work_item": {"id": "w1", "key": "DESK-7.1", "title": "t"}}
    return {
        "title": _render(template, action.title, fields),
        "justification": _render(template, action.justification, fields) if action.justification else None,
        "params": _render(template, action.params, fields),
        "approval": rendered_approval(action.approval.model_dump(), context) if action.approval else None,
        "effects": action.effects,
    }


GRANT_FIELDS = {**GRANT, "requester": "bob@acme.test", "request": "DESK-7"}


@both
@pytest.mark.parametrize("sensitive", [False, True])
def test_the_grant_waits_for_the_tools_approvers_never_for_the_people_it_concerns(
    template: str, sensitive: bool
) -> None:
    proposed = _proposed(template, "access-grant", "t-grant", {**GRANT_FIELDS, "sensitive": sensitive})
    assert proposed["title"].startswith("[sensitive] ") is sensitive
    assert (
        proposed["title"]
        .removeprefix("[sensitive] ")
        .startswith(f"lea@acme.test into group {GRANT['group_id']} (")
    )
    assert "requested by bob@acme.test, request DESK-7" in proposed["justification"]
    assert proposed["approval"]["approvers"] == [
        {"role": "approver", "group": "access-approvers-crm", "min": 1}
    ]
    assert proposed["approval"]["excluded"] == ["lea@acme.test", "bob@acme.test"]
    assert proposed["approval"]["step_up_minutes"] <= 5
    (call, *rest) = proposed["effects"]
    assert not rest, "the grant writes the directory, and nothing else: a reply is its own action"
    from tranibor_api.effects import render

    assert render(call.with_["arguments"], {"params": proposed["params"], "effects": []}) == {
        "upn": "lea@acme.test",
        "group_id": GRANT["group_id"],
    }
    assert call.compensate is not None and call.compensate["with"]["operation"] == "remove_from_group"


@both
def test_a_grant_with_no_approvers_group_is_not_proposed(template: str) -> None:
    from tranibor_api.effects import EffectRefused

    with pytest.raises(EffectRefused):
        _proposed(template, "access-grant", "t-grant", {**GRANT_FIELDS, "approver_group": ""})


@both
def test_the_bug_is_filed_in_its_project_once_the_desk_approves_never_by_who_reported_it(
    template: str,
) -> None:
    fields = {**BUG, "requester": "bob@acme.test", "request": "DESK-9"}
    proposed = _proposed(template, "bug-report", "t-file", fields)
    assert proposed["justification"] == BUG["report"], "the approver reads the report before it leaves"
    assert proposed["approval"]["approvers"] == [{"role": "contributor", "group": "it-desk", "min": 1}]
    assert proposed["approval"]["excluded"] == ["bob@acme.test"]
    (call,) = proposed["effects"]
    assert call.effect == "work_item.file"
    from tranibor_api.effects import render

    filed = render(call.with_, {"params": proposed["params"], "effects": []})
    assert (filed["project"], filed["title"], filed["labels"]) == ("expenses", BUG["title"], ["bug"])
    assert "Reported by bob@acme.test (request DESK-9)" in filed["body"]


@both
def test_a_failed_write_is_not_proposed_again_on_its_own(template: str) -> None:
    for workflow, transition in (("access-grant", "t-grant"), ("bug-report", "t-file")):
        found = _transition(template, workflow, transition)
        assert found.on_fail is not None
        assert (found.on_fail.max_attempts, found.on_fail.escalate_to) == (1, "needs_review"), workflow


@both
def test_a_person_step_waits_30_days_and_only_then_ends_as_rejected(template: str) -> None:
    for name in ("access-request", "access-grant", "bug-report"):
        workflow = _workflow(template, name)
        people = {n for n, actor in workflow.actors.items() if actor.type == "human"}
        for transition in workflow.transitions:
            if transition.by in people and transition.on_reject:
                assert transition.timeout_hours == 720, f"{template}/{name}/{transition.id}"


# ── The replies (Microsoft 365): fixed text, identifiers, or a person's answer ─────────────────────


def _replies(template: str) -> list[tuple[str, str, Any]]:
    found = []
    for name in ("access-request", "access-grant", "bug-report"):
        for transition in _workflow(template, name).transitions:
            for effect in transition.action.effects if transition.action else []:
                if effect.with_.get("connector") == "desk":
                    found.append((name, transition.id, effect))
    return found


def test_only_the_m365_template_answers_and_it_answers_every_outcome() -> None:
    assert _replies(BASE) == []
    told = {(name, transition) for name, transition, _ in _replies(M365)}
    assert told == {
        ("access-request", "t-send-answer"),
        ("access-grant", "t-tell-granted"),
        ("access-grant", "t-tell-refused"),
        ("bug-report", "t-tell-filed"),
    }
    grant = _workflow(M365, "access-grant")
    assert _transition(M365, "access-grant", "t-grant").on_reject == "telling_refusal"
    assert grant.states["refused"].terminal and grant.states["granted"].terminal


#: What the agent wrote, carried in the fields a reply's action could read: none may reach a reply.
AGENT_TEXT = {
    "tool": "AGENT-TOOL",
    "group_name": "AGENT-GROUP",
    "reason": "AGENT-REASON",
    "title": "AGENT-TITLE",
    "report": "AGENT-REPORT",
}


@pytest.mark.parametrize(("workflow", "transition"), [
    ("access-grant", "t-tell-granted"), ("access-grant", "t-tell-refused"), ("bug-report", "t-tell-filed"),
])  # fmt: skip
def test_a_reply_carries_fixed_text_and_identifiers_never_the_agents_words(
    workflow: str, transition: str
) -> None:
    from tranibor_api.effects import render

    fields = {**GRANT_FIELDS, **BUG, **AGENT_TEXT}
    proposed = _proposed(M365, workflow, transition, fields)
    (call,) = proposed["effects"]
    assert (call.with_["connector"], call.with_["operation"]) == ("desk", "reply")
    arguments = render(call.with_["arguments"], {"params": proposed["params"], "effects": []})
    assert arguments["key"] == fields["request"], "the reply goes to the thread the request came from"
    leaked = [v for v in AGENT_TEXT.values() if v in arguments["text"]]
    assert not leaked, leaked
    assert proposed["approval"] is None, (
        "the organisation's policy of `desk/reply` decides (approval by default)"
    )


def test_the_answer_a_person_wrote_is_what_is_sent() -> None:
    from tranibor_api.effects import render

    fields = {"answer": "Your laptop is ordered.", "summary": "AGENT-SUMMARY"}
    proposed = _proposed(M365, "access-request", "t-send-answer", fields)
    (call,) = proposed["effects"]
    arguments = render(call.with_["arguments"], {"params": proposed["params"], "effects": []})
    assert arguments == {"key": "DESK-7.1", "text": "Your laptop is ordered."}
    answer = _transition(M365, "access-request", "t-answer")
    assert answer.task is not None and set(answer.task.form["properties"]) == {"answer"}
    assert answer.on_reject == "declined"


def test_a_reply_is_its_own_action_so_its_failure_never_undoes_a_write() -> None:
    for workflow, write, tell in (
        ("access-grant", "t-grant", "t-tell-granted"),
        ("bug-report", "t-file", "t-tell-filed"),
    ):
        assert all(
            e.with_.get("connector") != "desk" for e in _transition(M365, workflow, write).action.effects
        )
        told = _transition(M365, workflow, tell)
        assert told.on_fail is not None and told.on_fail.escalate_to in {"granted", "filed"}, (
            "a reply that fails twice leaves the write done"
        )


def test_no_reply_text_reads_a_field_the_agent_writes() -> None:
    agent_fields = {"tool", "group_name", "reason", "title", "report", "summary"}
    for _, transition, effect in _replies(M365):
        text = str(effect.with_["arguments"]["text"])
        params = set(re.findall(r"params\.([a-z_]+)", text))
        assert not params & agent_fields, (transition, params)
