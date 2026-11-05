# SPDX-License-Identifier: Apache-2.0
"""The plugin's matrix cases (`evals/matrix/`, the core's agent-case format, S57-09) played through the
core's REAL doors on this server's fake instance, and judged by the core's case judge.

The core's scripted matrix plays an agent case with `eval_doors.play`, whose fake API (`EvalApi`) builds a
run's tools from the case's connectors, each a fake SCENE of the core's adapters
(`choregos_adapters.fakes.scenes`). A plugin's server is not a scene (core issue #1372), so this test
gives the same `EvalApi` this server's tools instead: each operation the agent declares, its access from
`servers/servicenow.yaml` and the case's policy as `EvalApi` derives it for a scene, its input schema the
one the server lists, its call the server's tool through MCP in-process on the seeded fake instance
(`SERVICENOW_FAKE=1`). The rest is the core's: the `mcp` cell's evidence through the run's MCP server
(`through_mcp`), the `scripts` cell's script run with `python3 -S`, the generated `choregos_tools` and the
real tool bridge (`through_the_bridge`), the answer (`answer_of`), the scripts (`scripts_of`), and the
verdict (`choregos_playbooks.evals.agents.judge`), the model requests being the case's `scripted_usage`
as the scripted matrix bills them on its fake gateway.

It needs the core's workspace (`uv sync --frozen` in a core checkout) and this server's `src` on
`PYTHONPATH`: CI's `matrix` job runs it so. In the server's own environment it is skipped.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from mcp import Client

from choregos_servicenow.app import Runtime, build_server
from choregos_servicenow.config import Settings
from choregos_servicenow.fake import FakeServiceNow, sys_id_of

eval_doors = pytest.importorskip(
    "choregos_orchestrator.activities.eval_doors", reason="the core's door player: run in the core's workspace"
)
agents = pytest.importorskip(
    "choregos_playbooks.evals.agents", reason="the core's case judge: run in the core's workspace"
)

PLUGIN = Path(__file__).resolve().parents[2]
MATRIX = PLUGIN / "evals" / "matrix"
E3, C1 = "itsm-triage-similar-incidents", "itsm-triage-vague-incident"
CONNECTOR = {"kind": "plugin:servicenow/servicenow", "scene": "default"}


def _yaml(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


AGENT = _yaml(PLUGIN / "agents" / "itsm-triager.yaml")["agent"]
OPERATIONS = {op["name"]: op for op in _yaml(PLUGIN / "servers" / "servicenow.yaml")["operations"]}
#: The tools a run of the agent gets from the server: what its `mcp_servers` entry declares.
AGENT_TOOLS = next(s["tools"] for s in AGENT["spec"]["mcp_servers"] if s["connector"] == "servicenow")


def case_of(name: str) -> dict[str, Any]:
    case: dict[str, Any] = agents.load_case(MATRIX / f"{name}.yaml")
    return case


class FakeServer:
    """This server on its seeded fake instance, as the connector `EvalApi` calls:
    `execute(operation, arguments)` is the tool of that name, through MCP in-process."""

    def __init__(self) -> None:
        self.runtime = Runtime(Settings.from_env({"SERVICENOW_FAKE": "1"}))
        self.server = build_server(self.runtime)
        assert self.runtime.fake is not None
        self.fake: FakeServiceNow = self.runtime.fake

    async def listed(self) -> dict[str, tuple[str, dict[str, Any]]]:
        async with Client(self.server) as client:
            tools = (await client.list_tools()).tools
        return {tool.name: (tool.description or tool.name, dict(tool.input_schema)) for tool in tools}

    async def execute(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from choregos_adapters.errors import AdapterError

        async with Client(self.server) as client:
            result = await client.call_tool(operation, arguments)
        if result.is_error:
            raise AdapterError(" ".join(str(getattr(block, "text", block)) for block in result.content))
        return dict(result.structured_content or {})


async def eval_api(case: dict[str, Any]) -> tuple[Any, FakeServer]:
    """`EvalApi` for the case, its `servicenow` connector this server on the fake instead of a scene."""
    assert case.get("connectors") == {"servicenow": CONNECTOR}, "one connector: this plugin's server"
    api = eval_doors.EvalApi({**case, "connectors": {}})
    server = FakeServer()
    listed = await server.listed()
    writes = str((case.get("policy") or {}).get("writes", "approval"))
    for name in AGENT_TOOLS:
        operation = OPERATIONS[name]
        # As `EvalApi.__post_init__` for a scene's operation: `human_approval` is never lowered.
        access = "read"
        if operation["access"] != "read":
            access = "approval" if operation.get("human_approval") or writes != "allowed" else "write"
        description, schema = listed[name]
        if access == "approval":
            description += " — needs a human approval: calling it proposes the action, it does not run it"
        elif access == "write":
            description += " — runs as a governed action: journaled, done once, undone if a later step fails"
        tool = f"servicenow__{name}"
        api.tools[tool] = eval_doors.Tool(tool, f"[servicenow] {description}", schema, access, server, name)
    return api, server


@dataclass
class Played:
    answer: str
    observation: dict[str, Any]
    door: str
    server: FakeServer
    script: dict[str, Any] | None = None


async def play(case: dict[str, Any], cell: str, workdir: Path) -> Played:
    """`eval_doors.play`, on `eval_api`'s tools; the requests are what the scripted matrix bills."""
    api, server = await eval_api(case)
    script = case.get("scripted_script") if cell == "scripts" else None
    if script is None:
        await eval_doors.through_mcp(api, [c for c in case.get("evidence") or [] if "tool" in c])
        played = Played(eval_doors.answer_of(case, None), api.observation(cell), "mcp", server)
    else:
        ran = await eval_doors.through_the_bridge(api, script, workdir)
        played = Played(eval_doors.answer_of(case, ran["stdout"]), api.observation(cell), "bridge", server, ran)
        played.observation["scripts"] = eval_doors.scripts_of(ran)
    played.observation["requests"] = case["scripted_usage"][cell]["requests"]
    return played


def _journal(fake: FakeServiceNow, number: str, element: str) -> list[str]:
    return [str(r["value"]) for r in fake.journal(sys_id_of(number)) if r["element"] == element]


# ─────────────────────────── the cases, as the core reads them ───────────────────────────


@pytest.mark.parametrize("name", [E3, C1])
def test_a_case_is_in_the_core_s_format_and_names_the_plugin_s_agent_and_tools(name: str) -> None:
    case = case_of(name)
    assert case["name"] == name
    assert case["agent"] == AGENT["slug"]
    assert set(case["tools_cells"]) == set(agents.TOOLS_CELLS)
    assert set(case["scripted_usage"]) == set(case["tools_cells"])
    assert agents.score_instructions(case, AGENT["spec"]["instructions"]) == [], "the agent's instructions"
    assert {kind["kind"] for kind in case["assertions"]} <= set(agents.ASSERTION_KINDS)
    everything = {"tools": "mcp", "requests": 0, "calls": [], "actions": [], "security_events": []}
    assert not [r for r in agents.check_assertions(case, everything) if r.startswith("unknown")]
    assert {call["tool"] for call in case["evidence"]} <= {f"servicenow__{tool}" for tool in AGENT_TOOLS}
    # The answer the `mcp` cell gives, and the shape of every answer: its output, its four sections.
    assert agents.score_answer(case, case["reference_answer"]) == (1.0, [], [], False)


# ─────────────────────────── played through the core's doors ───────────────────────────


@pytest.mark.parametrize(("name", "cell"), [(E3, "mcp"), (E3, "scripts"), (C1, "mcp"), (C1, "scripts")])
async def test_a_case_passes_the_core_s_judge_in_each_of_its_cells(name: str, cell: str, tmp_path: Path) -> None:
    case = case_of(name)
    played = await play(case, cell, tmp_path)
    if played.script is not None:
        assert played.script["exit_code"] == 0, played.script["stderr"]
    refused = [c for c in played.observation["calls"] if c["status_code"] not in (200, 202)]
    assert refused == [], "every call the case makes is one the server and the platform serve"
    verdict = agents.judge(case, played.answer, played.observation)
    assert verdict["ok"], verdict["reason"]


async def test_e3_s_script_fans_out_through_the_bridge_and_prints_only_the_resolutions(tmp_path: Path) -> None:
    case = case_of(E3)
    played = await play(case, "scripts", tmp_path)
    assert played.door == "bridge" and played.script is not None
    calls = played.observation["calls"]
    assert [c["origin"] for c in calls] == ["script"] * 25, "2 reads, 10 times 2 reads, the knowledge, 2 writes"
    [script] = played.observation["scripts"]
    assert (script["calls"], script["failed_calls"], script["errored"]) == (25, 0, False)
    stdout = played.script["stdout"]
    assert stdout.startswith("10 similar incidents resolved or closed in the last 90 days, and how:\n")
    assert "CHG0003311 resolved 2 of them: INC0012102, INC0012160\n" in stdout
    assert "KB0010042: VPN to the Lyon site drops: check the MTU on the Lyon concentrator\n" in stdout
    assert "- servicenow__add_work_note: written\n" in stdout
    assert "- servicenow__add_comment: PROPOSED, NOT DONE: servicenow__add_comment" in stdout
    assert [(a["tool"], a["status"]) for a in played.observation["actions"]] == [
        ("servicenow__add_work_note", "succeeded"),
        ("servicenow__add_comment", "pending_approval"),
    ]
    # On the instance: the work note was written; the comment was not, nothing was resolved.
    notes = _journal(played.server.fake, "INC0012345", "work_notes")
    assert len(notes) == 2 and "CHG0003311 resolved INC0012102, INC0012160" in notes[-1]
    assert _journal(played.server.fake, "INC0012345", "comments") == []
    assert played.server.fake.by_number("INC0012345")["state"] == "2"


async def test_e3_through_the_mcp_door_makes_the_same_calls_as_the_model(tmp_path: Path) -> None:
    played = await play(case_of(E3), "mcp", tmp_path)
    assert played.door == "mcp"
    assert [c["origin"] for c in played.observation["calls"]] == ["model"] * 25
    assert _journal(played.server.fake, "INC0012345", "comments") == []


async def test_e3_fails_when_the_work_note_waits_for_a_person(tmp_path: Path) -> None:
    """`executed_actions` bites: under a policy where every write waits, the work note is only proposed."""
    case = {**copy.deepcopy(case_of(E3)), "policy": {"writes": "approval"}}
    played = await play(case, "scripts", tmp_path)
    verdict = agents.judge(case, played.answer, played.observation)
    assert not verdict["ok"]
    assert verdict["assertions_failed"] == ["executed_actions: 0, expected 1"]
    assert len(_journal(played.server.fake, "INC0012345", "work_notes")) == 1, "only the seeded note"


async def test_c1_fails_when_the_vague_incident_is_resolved(tmp_path: Path) -> None:
    """`proposed_actions` and `no_tool_called` bite: a run that resolves the vague incident fails, and the
    resolution, which a person approves, is not played."""
    case = copy.deepcopy(case_of(C1))
    case["evidence"].append(
        {
            "tool": "servicenow__resolve_incident",
            "arguments": {
                "sys_id": sys_id_of("INC0012399"),
                "close_code": "Solution provided",
                "close_notes": "See KB0010003.",
            },
        }
    )
    played = await play(case, "mcp", tmp_path)
    verdict = agents.judge(case, played.answer, played.observation)
    assert not verdict["ok"]
    assert verdict["assertions_failed"] == [
        "proposed_actions: 1, expected 0",
        "no_tool_called: called servicenow__resolve_incident",
    ]
    assert played.server.fake.by_number("INC0012399")["state"] == "1"
