# SPDX-License-Identifier: Apache-2.0
"""The plugin's matrix cases (`contents.matrix_cases`, core #1372) played as the core's scripted eval
matrix plays them, against this server in-process on its fake, and judged by the core's case judge.

The matrix names a case `plugin:procurement/<file stem>` and finds it in the offline plugins
(`CHOREGOS_PLUGINS_OFFLINE`, here this repository's `plugins/`): what `plugin.yaml` declares is what it
plays. The case's connector `plugin:procurement/purchasing` is this server, which the core reaches as the
broker does, over MCP with the platform's bearer token, on the fake registered for it in the process
(`tranibor_adapters.fakes.plugin_servers.use_fake_plugin_server`). Registered here: the server's own app
(`build_app`) started as the platform starts its eval instance, with the variables of its `fake`
declaration (`servers/purchasing.yaml`), the case's scene (`PURCHASING_FAKE_SCENE`) and the platform's
token, nothing of the configuration, behind an `httpx.ASGITransport`.

The rest is the core's: the server's declaration governs its tools (`EvalApi.ready`), `eval_doors.play`
plays the case through its cell's door (these cases have no script: both cells play the MCP door), and
`tranibor_playbooks.evals.agents.judge` gives the verdict.

It needs the core's workspace (`uv sync --frozen` in a core checkout) and this server's `src` on
`PYTHONPATH`: CI's `matrix` job runs it so. In the server's own environment it is skipped.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from starlette.applications import Starlette

from tranibor_purchasing.app import build_app
from tranibor_purchasing.backends.fake import FakePurchasing
from tranibor_purchasing.config import Settings

eval_doors = pytest.importorskip(
    "tranibor_orchestrator.activities.eval_doors", reason="the core's door player: run in the core's workspace"
)
evals = pytest.importorskip("tranibor_orchestrator.activities.evals", reason="the core's eval matrix")
agents = pytest.importorskip("tranibor_playbooks.evals.agents", reason="the core's case judge")
offline = pytest.importorskip("tranibor_core.agent_catalogue", reason="the core's offline plugins")
plugin_servers = pytest.importorskip("tranibor_adapters.fakes.plugin_servers", reason="the core's plugin fakes")

PLUGIN = Path(__file__).resolve().parents[2]
PLUGINS = PLUGIN.parent
SERVES, NONE, RENEWAL = (
    "request-agreement-serves-need",
    "request-no-agreement-serves-need",
    "renewal-memo-reads-the-contract",
)
CASES = {SERVES: "request-qualifier", NONE: "request-qualifier", RENEWAL: "renewal-analyst"}
#: The scene each case plays on, and the one tool call it makes (its evidence is the model's whole run).
SCENES = {SERVES: "default", NONE: "default", RENEWAL: "expiring-contracts"}
CALLS = {
    SERVES: ["purchasing__list_framework_agreements"],
    NONE: ["purchasing__list_framework_agreements"],
    RENEWAL: ["purchasing__get_contract"],
}
#: This case's connector: this plugin's server, as the matrix names it.
SERVER = "plugin:procurement/purchasing"
WRITES = ["create_supplier", "update_supplier", "archive_supplier", "add_contract_note"]
WRITES += ["create_purchase_request", "cancel_purchase_request"]


def agent_of(slug: str) -> dict[str, Any]:
    document = yaml.safe_load((PLUGIN / "agents" / f"{slug}.yaml").read_text(encoding="utf-8"))
    agent: dict[str, Any] = document["agent"]
    return agent


class OnItsFake(httpx.AsyncBaseTransport):
    """This server in-process, as its eval instance runs: its app behind `httpx.ASGITransport`, the app's
    lifespan (the MCP session manager) started on the first request and stopped by `close`, and `fake`,
    the seeded purchasing system it serves, which the core hands back (`PluginScene.fake`)."""

    def __init__(self, app: Starlette) -> None:
        self.app = app
        self.fake: FakePurchasing = app.state.runtime.fake
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
async def purchasing() -> AsyncIterator[list[OnItsFake]]:
    """This server registered as the fake of `procurement/purchasing`, a fresh one per case played, each
    started with its `fake` variables, the scene the case asks for and the platform's token, as the
    platform starts its eval instance."""
    declaration = offline.catalogue().server(SERVER)
    assert declaration is not None, "plugin.yaml ships servers/purchasing.yaml"
    assert "fake" in declaration, "servers/purchasing.yaml declares its `fake`, or no live cell plays the cases"
    started: list[OnItsFake] = []

    def factory(scene: str) -> httpx.AsyncBaseTransport:
        assert scene in plugin_servers.declared_scenes(declaration), f"its fake serves the scene {scene}"
        environment = {
            **declaration["fake"]["env"],
            "PURCHASING_FAKE_SCENE": scene,
            "CHOREGOS_SERVER_TOKEN": plugin_servers.EVAL_TOKEN,
        }
        started.append(OnItsFake(build_app(Settings.from_env(environment))))
        return started[-1]

    plugin_servers.use_fake_plugin_server("procurement", "purchasing", factory)
    yield started
    plugin_servers.use_fake_plugin_server("procurement", "purchasing", None)
    for server in started:
        await server.close()


def load(name: str) -> dict[str, Any]:
    """The case as the scripted matrix loads it: `plugin:procurement/<name>`, from the offline plugins."""
    case: dict[str, Any] | None = evals.load_agent_case(f"plugin:procurement/{name}")
    assert case is not None, f"plugin.yaml declares evals/matrix/{name}.yaml in `contents.matrix_cases`"
    return case


async def play(case: dict[str, Any], cell: str, workdir: Path) -> tuple[Any, dict[str, Any]]:
    """`eval_doors.play`, then the core's verdict, the requests what the scripted matrix bills."""
    played = await eval_doors.play(case, cell, workdir=workdir)
    observation = {**played.observation, "requests": case["scripted_usage"][cell]["requests"]}
    return played, agents.judge(case, played.answer, observation)


def instance(played: Any) -> FakePurchasing:
    """The fake the case's server served (`PluginScene.fake`: this test's transport's)."""
    fake: FakePurchasing = played.api.scenes["purchasing"].fake
    return fake


def answered(case: dict[str, Any], **changes: str) -> str:
    """The case's reference answer with `changes` written over its top-level keys (JSON), as a model that
    got it wrong would write it."""
    document = json.loads(case["reference_answer"])
    document.update(changes)
    return json.dumps(document)


# ─────────────────────────── the cases, as the matrix finds them ───────────────────────────


def test_the_matrix_finds_every_case_of_evals_matrix_and_the_server_s_fake() -> None:
    view = offline.catalogue()
    assert "procurement" in view.plugins, "the plugin loads (`tranibor plugin validate plugins/procurement`)"
    declared = sorted(c.name for c in view.matrix_cases if c.plugin == "procurement")
    assert declared == sorted(p.stem for p in (PLUGIN / "evals" / "matrix").glob("*.yaml")) == sorted(CASES)
    server = view.server(SERVER)
    assert server is not None
    assert plugin_servers.declared_scenes(server) == ("default", "duplicate-supplier", "expiring-contracts")


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_case_is_in_the_core_s_format_and_names_the_plugin_s_agent_and_tools(name: str) -> None:
    case = load(name)
    agent = agent_of(CASES[name])
    assert (case["name"], case["plugin"], case["agent"]) == (name, "procurement", agent["slug"])
    assert case["connectors"] == {"purchasing": {"kind": SERVER, "scene": SCENES[name]}}
    assert set(case["tools_cells"]) == set(agents.TOOLS_CELLS)
    assert set(case["scripted_usage"]) == set(case["tools_cells"])
    instructions = agents.case_instructions(case)
    assert instructions is not None, "the plugin's agent, from the offline plugins"
    assert agents.score_instructions(case, instructions) == [], "the agent's instructions"
    assert {kind["kind"] for kind in case["assertions"]} <= set(agents.ASSERTION_KINDS)
    everything = {"tools": "mcp", "requests": 0, "calls": [], "actions": [], "security_events": []}
    assert not [r for r in agents.check_assertions(case, everything) if r.startswith("unknown")]
    tools = next(s["tools"] for s in agent["spec"]["mcp_servers"] if s["connector"] == "purchasing")
    assert {call["tool"] for call in case["evidence"]} <= {f"purchasing__{tool}" for tool in tools}
    assert agents.score_answer(case, case["reference_answer"]) == (1.0, [], [], False)


# ─────────────────────────── played through the core's doors ───────────────────────────


@pytest.mark.parametrize("cell", ["mcp", "scripts"])
@pytest.mark.parametrize("name", sorted(CASES))
async def test_a_case_passes_the_core_s_judge_in_each_of_its_cells(
    purchasing: list[OnItsFake], name: str, cell: str, tmp_path: Path
) -> None:
    played, verdict = await play(load(name), cell, tmp_path)
    assert played.door == "mcp", "no case has a script: both cells play the model's door"
    refused = [c for c in played.observation["calls"] if c["status_code"] not in (200, 202)]
    assert refused == [], "every call the case makes is one the server and the platform serve"
    assert [c["tool"] for c in played.observation["calls"]] == CALLS[name], "the tool is called, and only it"
    assert verdict["ok"], verdict["reason"]
    assert [server.fake for server in purchasing] == [instance(played)], "this server, on its fake"
    assert instance(played).writes == [], "nothing was written in the purchasing system"
    assert instance(played).scene == SCENES[name]


# ─────────────────────────── each guarantee bites ───────────────────────────


async def test_a_run_that_writes_fails_every_case(purchasing: list[OnItsFake], tmp_path: Path) -> None:
    """`proposed_actions`, `executed_actions` and `no_tool_called` bite: a run that also opens a purchase
    request fails, in the case of each agent, and the write, which a person approves, is not played."""
    for name in (SERVES, RENEWAL):
        case = load(name)
        case["evidence"].append(
            {
                "tool": "purchasing__create_purchase_request",
                "arguments": {
                    "supplier_id": "SUP-0001",
                    "title": "Toner",
                    "lines": [{"description": "Toner", "quantity": "1", "unit_price": "10.00"}],
                },
            }
        )
        played, verdict = await play(case, "mcp", tmp_path)
        assert not verdict["ok"]
        assert sorted(verdict["assertions_failed"]) == [
            "no_tool_called: called purchasing__create_purchase_request",
            "proposed_actions: 1, expected 0",
        ]
        assert instance(played).writes == []


async def test_the_judge_refuses_an_answer_that_does_not_cite_the_agreement(
    purchasing: list[OnItsFake], tmp_path: Path
) -> None:
    """The agreement is cited: an answer that routes to it without its id, or to another one, fails."""
    case = load(SERVES)
    played, verdict = await play(case, "mcp", tmp_path)
    assert verdict["ok"]
    for wrong in ("FA-0003", "FA-0002", "FA-0004"):
        answer = answered(case, route="framework_agreement").replace("FA-0001", wrong)
        failed = agents.judge(case, answer, played.observation | {"requests": 3})
        assert not failed["ok"], wrong
    for route in ("tender", "catalogue", "three_quotes"):
        answer = answered(case).replace('"route": "framework_agreement"', f'"route": "{route}"')
        assert not agents.judge(case, answer, played.observation | {"requests": 3})["ok"], route


async def test_the_judge_refuses_an_agreement_nobody_listed(purchasing: list[OnItsFake], tmp_path: Path) -> None:
    """No agreement is invented: an answer that names one when none serves the need fails, whichever it is."""
    case = load(NONE)
    played, verdict = await play(case, "mcp", tmp_path)
    assert verdict["ok"]
    observation = played.observation | {"requests": 3}
    for invented in ("FA-0001", "FA-0007"):
        answer = answered(case).replace('"agreement_id": null', f'"agreement_id": "{invented}"')
        assert "forbidden in the answer" in agents.judge(case, answer, observation)["reason"], invented
    answer = answered(case).replace('"route": "tender"', '"route": "framework_agreement"')
    assert not agents.judge(case, answer, observation)["ok"]


async def test_the_judge_refuses_a_memo_that_recommends_or_drops_a_section(
    purchasing: list[OnItsFake], tmp_path: Path
) -> None:
    case = load(RENEWAL)
    played, verdict = await play(case, "mcp", tmp_path)
    assert verdict["ok"]
    observation = played.observation | {"requests": 3}
    memo = json.loads(case["reference_answer"])["renewal_memo_markdown"]
    for wrong in (
        memo + "\nWe recommend renewing it.\n",
        memo.replace("## Options", "## Choices"),
        memo.replace("36000.00", "35000.00"),
    ):
        answer = json.dumps({"renewal_memo_markdown": wrong})
        assert not agents.judge(case, answer, observation)["ok"], wrong[-40:]
