# SPDX-License-Identifier: Apache-2.0
"""The plugin's matrix cases (`contents.matrix_cases`, core #1372) played as the core's scripted eval
matrix plays them, against this server in-process on its fake, and judged by the core's case judge.

The matrix names a case `plugin:servicenow/<file stem>` and finds it in the offline plugins
(`CHOREGOS_PLUGINS_OFFLINE`, here this repository's `plugins/`): what `plugin.yaml` declares is what it
plays. The case's connector `plugin:servicenow/servicenow` is this server, which the core reaches as the
broker does, over MCP with the platform's bearer token, on the fake registered for it in the process
(`choregos_adapters.fakes.plugin_servers.use_fake_plugin_server`). Registered here: the server's own app
(`build_app`) started as the platform starts its eval instance, with the variables of its `fake`
declaration (`servers/servicenow.yaml`) and the platform's token, nothing of the configuration, behind an
`httpx.ASGITransport`.

The rest is the core's: the server's declaration governs its tools (`EvalApi.ready`: the declared
operations only, their access, `human_approval` never lowered, `action_key` filled by the platform),
`eval_doors.play` plays the case through its cell's door (the `mcp` cell's evidence through the run's MCP
server; the `scripts` cell's script with `python3 -S`, the generated `choregos_tools` and the real tool
bridge), and `choregos_playbooks.evals.agents.judge` gives the verdict, the model requests being the
case's `scripted_usage` as the scripted matrix bills them.

It needs the core's workspace (`uv sync --frozen` in a core checkout, v0.23.2 or later) and this server's
`src` on `PYTHONPATH`: CI's `matrix` job runs it so. In the server's own environment it is skipped.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from starlette.applications import Starlette

from choregos_servicenow.app import build_app
from choregos_servicenow.config import Settings
from choregos_servicenow.fake import FakeServiceNow, sys_id_of

eval_doors = pytest.importorskip(
    "choregos_orchestrator.activities.eval_doors", reason="the core's door player: run in the core's workspace"
)
evals = pytest.importorskip("choregos_orchestrator.activities.evals", reason="the core's eval matrix")
agents = pytest.importorskip("choregos_playbooks.evals.agents", reason="the core's case judge")
offline = pytest.importorskip("choregos_core.agent_catalogue", reason="the core's offline plugins")
plugin_servers = pytest.importorskip("choregos_adapters.fakes.plugin_servers", reason="the core's plugin fakes")

PLUGIN = Path(__file__).resolve().parents[2]
PLUGINS = PLUGIN.parent
E3, C1 = "itsm-triage-similar-incidents", "itsm-triage-vague-incident"
#: The case's connector: this plugin's server, as the matrix names it.
SERVER = "plugin:servicenow/servicenow"

AGENT = yaml.safe_load((PLUGIN / "agents" / "itsm-triager.yaml").read_text(encoding="utf-8"))["agent"]
#: The tools a run of the agent gets from the server: what its `mcp_servers` entry declares.
AGENT_TOOLS = next(s["tools"] for s in AGENT["spec"]["mcp_servers"] if s["connector"] == "servicenow")


class OnItsFake(httpx.AsyncBaseTransport):
    """This server in-process, as its eval instance runs: its app behind `httpx.ASGITransport`, the app's
    lifespan (the MCP session manager) started on the first request and stopped by `close`, and `fake`,
    the seeded instance it serves, which the core hands back (`PluginScene.fake`)."""

    def __init__(self, app: Starlette) -> None:
        self.app = app
        self.fake: FakeServiceNow = app.state.runtime.fake
        self._asgi = httpx.ASGITransport(app=app)
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._serving: asyncio.Task[None] | None = None

    async def _lifespan(self, started: asyncio.Event) -> None:
        async with self.app.router.lifespan_context(self.app):
            started.set()
            await self._stop.wait()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with self._lock:
            if self._serving is None:
                started = asyncio.Event()
                self._serving = asyncio.create_task(self._lifespan(started))
                waiting = asyncio.create_task(started.wait())
                await asyncio.wait({self._serving, waiting}, return_when=asyncio.FIRST_COMPLETED)
                waiting.cancel()
                if self._serving.done():
                    self._serving.result()  # the lifespan's failure
        return await self._asgi.handle_async_request(request)

    async def close(self) -> None:
        self._stop.set()
        if self._serving is not None:
            await self._serving


@pytest.fixture(autouse=True)
def offline_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """This repository's plugins, as the matrix reads its offline plugins."""
    monkeypatch.setenv(offline.OFFLINE_ENV, str(PLUGINS))


@pytest.fixture
async def servicenow() -> AsyncIterator[list[OnItsFake]]:
    """This server registered as the fake of `servicenow/servicenow`, a fresh one per case played, each
    started with its `fake` variables and the platform's token, as the platform starts its eval instance."""
    declaration = offline.catalogue().server(SERVER)
    assert declaration is not None, "plugin.yaml ships servers/servicenow.yaml"
    assert "fake" in declaration, "servers/servicenow.yaml declares its `fake`, or no live cell plays the cases"
    environment = {**declaration["fake"]["env"], "CHOREGOS_SERVER_TOKEN": plugin_servers.EVAL_TOKEN}
    started: list[OnItsFake] = []

    def factory(scene: str) -> httpx.AsyncBaseTransport:
        assert scene in plugin_servers.declared_scenes(declaration), f"its fake serves the scene {scene}"
        started.append(OnItsFake(build_app(Settings.from_env(environment))))
        return started[-1]

    plugin_servers.use_fake_plugin_server("servicenow", "servicenow", factory)
    yield started
    plugin_servers.use_fake_plugin_server("servicenow", "servicenow", None)
    for server in started:
        await server.close()


def load(name: str) -> dict[str, Any]:
    """The case as the scripted matrix loads it: `plugin:servicenow/<name>`, from the offline plugins."""
    case: dict[str, Any] | None = evals.load_agent_case(f"plugin:servicenow/{name}")
    assert case is not None, f"plugin.yaml declares evals/matrix/{name}.yaml in `contents.matrix_cases`"
    return case


async def play(case: dict[str, Any], cell: str, workdir: Path) -> tuple[Any, dict[str, Any]]:
    """`eval_doors.play`, then the core's verdict, the requests what the scripted matrix bills."""
    played = await eval_doors.play(case, cell, workdir=workdir)
    observation = {**played.observation, "requests": case["scripted_usage"][cell]["requests"]}
    return played, agents.judge(case, played.answer, observation)


def instance(played: Any) -> FakeServiceNow:
    """The fake instance the case's server served (`PluginScene.fake`: this test's transport's)."""
    fake: FakeServiceNow = played.api.scenes["servicenow"].fake
    return fake


def _journal(fake: FakeServiceNow, number: str, element: str) -> list[str]:
    return [str(r["value"]) for r in fake.journal(sys_id_of(number)) if r["element"] == element]


# ─────────────────────────── the cases, as the matrix finds them ───────────────────────────


def test_the_matrix_finds_every_case_of_evals_matrix_and_the_server_s_fake() -> None:
    view = offline.catalogue()
    assert "servicenow" in view.plugins, "the plugin loads (`choregos plugin validate plugins/servicenow`)"
    declared = sorted(c.name for c in view.matrix_cases if c.plugin == "servicenow")
    assert declared == sorted(p.stem for p in (PLUGIN / "evals" / "matrix").glob("*.yaml")) == sorted([E3, C1])
    server = view.server(SERVER)
    assert server is not None and plugin_servers.declared_scenes(server) == ("default",)


@pytest.mark.parametrize("name", [E3, C1])
def test_a_case_is_in_the_core_s_format_and_names_the_plugin_s_agent_and_tools(name: str) -> None:
    case = load(name)
    assert (case["name"], case["plugin"], case["agent"]) == (name, "servicenow", AGENT["slug"])
    assert case["connectors"] == {"servicenow": {"kind": SERVER, "scene": "default"}}
    assert set(case["tools_cells"]) == set(agents.TOOLS_CELLS)
    assert set(case["scripted_usage"]) == set(case["tools_cells"])
    instructions = agents.case_instructions(case)
    assert instructions is not None, "the plugin's agent, from the offline plugins"
    assert agents.score_instructions(case, instructions) == [], "the agent's instructions"
    assert {kind["kind"] for kind in case["assertions"]} <= set(agents.ASSERTION_KINDS)
    everything = {"tools": "mcp", "requests": 0, "calls": [], "actions": [], "security_events": []}
    assert not [r for r in agents.check_assertions(case, everything) if r.startswith("unknown")]
    assert {call["tool"] for call in case["evidence"]} <= {f"servicenow__{tool}" for tool in AGENT_TOOLS}
    # The answer the `mcp` cell gives, and the shape of every answer: its output, its four sections.
    assert agents.score_answer(case, case["reference_answer"]) == (1.0, [], [], False)


# ─────────────────────────── played through the core's doors ───────────────────────────


@pytest.mark.parametrize(("name", "cell"), [(E3, "mcp"), (E3, "scripts"), (C1, "mcp"), (C1, "scripts")])
async def test_a_case_passes_the_core_s_judge_in_each_of_its_cells(
    servicenow: list[OnItsFake], name: str, cell: str, tmp_path: Path
) -> None:
    played, verdict = await play(load(name), cell, tmp_path)
    if played.script is not None:
        assert played.script["exit_code"] == 0, played.script["stderr"]
    refused = [c for c in played.observation["calls"] if c["status_code"] not in (200, 202)]
    assert refused == [], "every call the case makes is one the server and the platform serve"
    assert verdict["ok"], verdict["reason"]
    assert [server.fake for server in servicenow] == [instance(played)], "this server, on its fake"


async def test_e3_s_script_fans_out_through_the_bridge_and_prints_only_the_resolutions(
    servicenow: list[OnItsFake], tmp_path: Path
) -> None:
    played, _ = await play(load(E3), "scripts", tmp_path)
    assert played.door == "bridge" and played.script is not None
    assert played.script["modules"] == ["servicenow"]
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
    fake = instance(played)
    notes = _journal(fake, "INC0012345", "work_notes")
    assert len(notes) == 2 and "CHG0003311 resolved INC0012102, INC0012160" in notes[-1]
    assert _journal(fake, "INC0012345", "comments") == []
    assert fake.by_number("INC0012345")["state"] == "2"


async def test_e3_through_the_mcp_door_makes_the_same_calls_as_the_model(
    servicenow: list[OnItsFake], tmp_path: Path
) -> None:
    played, _ = await play(load(E3), "mcp", tmp_path)
    assert played.door == "mcp"
    assert [c["origin"] for c in played.observation["calls"]] == ["model"] * 25
    assert _journal(instance(played), "INC0012345", "comments") == []


async def test_e3_fails_when_the_work_note_waits_for_a_person(servicenow: list[OnItsFake], tmp_path: Path) -> None:
    """`executed_actions` bites: under a policy where every write waits, the work note is only proposed."""
    played, verdict = await play({**load(E3), "policy": {"writes": "approval"}}, "scripts", tmp_path)
    assert not verdict["ok"]
    assert verdict["assertions_failed"] == ["executed_actions: 0, expected 1"]
    assert len(_journal(instance(played), "INC0012345", "work_notes")) == 1, "only the seeded note"


async def test_c1_fails_when_the_vague_incident_is_resolved(servicenow: list[OnItsFake], tmp_path: Path) -> None:
    """`proposed_actions` and `no_tool_called` bite: a run that resolves the vague incident fails, and the
    resolution, which a person approves, is not played."""
    case = load(C1)
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
    played, verdict = await play(case, "mcp", tmp_path)
    assert not verdict["ok"]
    assert verdict["assertions_failed"] == [
        "proposed_actions: 1, expected 0",
        "no_tool_called: called servicenow__resolve_incident",
    ]
    assert instance(played).by_number("INC0012399")["state"] == "1"
