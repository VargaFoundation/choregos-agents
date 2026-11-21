# SPDX-License-Identifier: Apache-2.0
"""`access-requests-m365` end to end on the real `desk` server, on the core's e2e bench.

The core's bench (Temporal in skipped time, the API in-process, fakes) runs the platform; this plugin's own
server (`build_app`, on its fake Microsoft 365) is the project's tracker and the `desk` connector, reached
through `use_plugin_transport`; the directory is the core's FakeEntra. Run it from a core checkout (CI's
`e2e` job does), the test copied into the core's `tests/e2e/`:

    cp <this repo>/plugins/microsoft-365-desk/e2e/test_m365_desk_on_the_core_bench.py <core>/tests/e2e/
    cd <core> && M365_DESK_PLUGIN_DIR=<this repo>/plugins/microsoft-365-desk \
        uv run --frozen pytest -q -p no:cacheprovider tests/e2e/test_m365_desk_on_the_core_bench.py

- A mail asking for the CRM: picked up by the tracker reconciliation, sorted (scripted agent), one grant
  child held in the platform, the CRM's approvers group decides (the person granted is refused), Entra is
  written, the requester is answered once in the mail's thread, the mail ends categorised `Choregos: Done`.
- A Teams mention: refused unsigned, accepted with the bot service's JWT, a person of the desk answers, the
  answer is posted in the thread through the Bot Connector.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from tranibor_contracts import StageResult, StageStatus

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

#: This plugin's directory in a checkout of the agents repository: the test is copied into the core's
#: `tests/e2e/` to run (it uses the core's e2e bench), so it is told where the plugin is.
PLUGIN_DIR = Path(os.environ.get("M365_DESK_PLUGIN_DIR", "")).resolve()
if not (PLUGIN_DIR / "plugin.yaml").is_file():
    raise RuntimeError("set M365_DESK_PLUGIN_DIR to the microsoft-365-desk plugin's directory")
sys.path.insert(0, str(PLUGIN_DIR / "server" / "src"))

ORG = "/api/v1/orgs/northwind"
KEY_DIRECTORY = "entra-app-secret"
SECOND = "second-admin@northwind.dev"
CRM_OWNER = "crm-owner@northwind.dev"
LEA = "lea@northwind.dev"
GROUP = "00000000-0000-0000-0000-000000000003"


class OnItsFake(httpx.AsyncBaseTransport):
    """The server's own app behind an ASGI transport, its lifespan (MCP session manager) running."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.fake = app.state.runtime.fake
        self._asgi = ASGITransport(app=app)
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._serving: asyncio.Task[None] | None = None
        self.requests: list[httpx.Request] = []

    async def _lifespan(self, started: asyncio.Event) -> None:
        async with self.app.router.lifespan_context(self.app):
            started.set()
            await self._stop.wait()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        async with self._lock:
            if self._serving is None:
                started = asyncio.Event()
                self._serving = asyncio.create_task(self._lifespan(started))
                waiting = asyncio.create_task(started.wait())
                await asyncio.wait({self._serving, waiting}, return_when=asyncio.FIRST_COMPLETED)
                waiting.cancel()
                if self._serving.done():
                    self._serving.result()
        return await self._asgi.handle_async_request(request)

    async def close(self) -> None:
        self._stop.set()
        if self._serving is not None:
            await self._serving


@pytest.fixture
def directory(monkeypatch: pytest.MonkeyPatch) -> Any:
    import tranibor_adapters
    from tranibor_adapters.fakes.entra import FakeEntra

    fake = FakeEntra(expected_secret=KEY_DIRECTORY)
    monkeypatch.setattr(tranibor_adapters, "FAKE_ENTRA", fake)
    monkeypatch.setenv("CONNECTOR_E2E_KEY_DIRECTORY", KEY_DIRECTORY)
    return fake


@pytest.fixture
async def kube(platform: Platform, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    from tranibor_adapters.fakes.kubernetes import FakeKubernetesApi
    from tranibor_adapters.plugin_servers import PluginServerRuntime
    from tranibor_api.config import reset_settings_cache
    from tranibor_api.services.plugin_servers import use_plugin_runtime, use_plugin_transport
    from tranibor_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    monkeypatch.setenv(
        "TRANIBOR_PLUGIN_SERVERS_REGISTRIES", json.dumps(["ghcr.io/graalsystems/tranibor-agents"])
    )
    monkeypatch.setenv("TRANIBOR_PLUGIN_FILES_DIR", str(tmp_path / "plugin-files"))
    reset_settings_cache()
    root = tmp_path / "plugins"
    target = root / "microsoft-365-desk" / "0.1.0"
    shutil.copytree(
        PLUGIN_DIR, target, ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.zip", "uv.lock")
    )
    use_plugin_supply(OfflineDirectory(root))
    api = FakeKubernetesApi()
    use_plugin_runtime(PluginServerRuntime(api, "choregos-plugins"))
    yield api
    use_plugin_runtime(None)
    use_plugin_transport(None)
    use_plugin_supply(None)


def _token(api: Any) -> str:
    from tranibor_adapters.plugin_servers import TOKEN_ENV

    tokens = {base64.b64decode(s["data"][TOKEN_ENV]).decode() for s in api.store("secrets").values()}
    (token,) = tokens
    return token


async def _member(client: AsyncClient, email: str, role: str, groups: list[str] | None = None) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import User
    from tranibor_api.db.session import session_scope

    added = await client.post(f"{ORG}/members", json={"email": email, "role": role})
    assert added.status_code in {200, 201}, added.text
    if groups is not None:
        async with session_scope() as session:
            user = (await session.execute(select(User).where(User.email == email))).scalar_one()
            user.groups = groups


async def _as(platform: Platform, email: str) -> AsyncClient:
    other = AsyncClient(transport=ASGITransport(app=platform.app), base_url="http://e2e")
    await login(other, email)
    return other


async def _organisation(platform: Platform, kube: Any, teams: bool = False) -> OnItsFake:
    from choregos_m365_desk.app import build_app
    from choregos_m365_desk.config import (
        FAKE_CHANNEL_ID,
        FAKE_CLIENT_ID,
        FAKE_MAILBOX,
        FAKE_TEAM_ID,
        FAKE_TENANT_ID,
        Settings,
    )
    from tranibor_api.services.plugin_servers import use_plugin_transport

    client = platform.client
    body = {"name": "directory", "kind": "identity", "type": "entra",
            "config": {"tenant_id": "acme", "client_id": "choregos"},
            "secret_refs": {"client_secret": "env:CONNECTOR_E2E_KEY_DIRECTORY"}}  # fmt: skip
    created = await client.post(f"{ORG}/connectors", json=body)
    assert created.status_code == 201, created.text

    asked = await client.post(
        f"{ORG}/plugins/install", json={"source": "offline", "name": "microsoft-365-desk", "version": "0.1.0"}
    )
    assert asked.status_code == 202, asked.text
    await _member(client, SECOND, "org_admin")
    second = await _as(platform, SECOND)
    try:
        approved = await second.post(f"{ORG}/plugin-approvals/{asked.json()['approval_id']}/approve")
        assert approved.status_code == 200, approved.text
    finally:
        await second.aclose()

    configured = await client.patch(
        f"{ORG}/connectors/desk",
        json={
            "config": {"tenant_id": FAKE_TENANT_ID, "client_id": FAKE_CLIENT_ID, "mailbox": FAKE_MAILBOX}
            | ({"teams": [{"team_id": FAKE_TEAM_ID, "channel_id": FAKE_CHANNEL_ID}]} if teams else {}),
            "secret_values": {"client_secret": "fake-client-secret"},
        },
    )
    assert configured.status_code == 200, configured.text
    desk = (await client.get(f"{ORG}/connectors/desk")).json()
    assert desk["status"] == "deployed", desk

    server = OnItsFake(
        build_app(Settings.from_env({"M365_DESK_FAKE": "1", "CHOREGOS_SERVER_TOKEN": _token(kube)}))
    )
    use_plugin_transport(server)

    opened = await client.patch(f"{ORG}/connectors/desk/operations/reply", json={"policy": "allowed"})
    assert opened.status_code == 200, opened.text

    await _member(client, CRM_OWNER, "approver", ["access-approvers-crm"])
    await _member(client, LEA, "approver", ["access-approvers-crm"])
    return server


async def _project(platform: Platform) -> dict[str, Any]:
    created = await platform.client.post(
        f"{ORG}/projects",
        json={"slug": "desk", "name": "Desk", "template_ref": "plugin:microsoft-365-desk/access-requests-m365",
              "config": {"slug": "desk", "org": "northwind"}},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _use_the_project_tracker(platform: Platform) -> Any:
    """HARNESS: the bench's adapter override replaces every project's tracker with the fake one; this
    project's tracker is put back: the core's plugin tracker adapter, on the real server."""
    from sqlalchemy import select
    from tranibor_api.db.models import Project
    from tranibor_api.db.session import session_scope
    from tranibor_api.services.tickets import project_tracker

    async with session_scope() as session:
        project = (await session.execute(select(Project).where(Project.slug == "desk"))).scalar_one()
        _, tracker = await project_tracker(session, project)
    assert tracker is not None
    platform.adapters.tracker = tracker
    return tracker


async def _reconcile(platform: Platform) -> dict[str, Any]:
    from temporalio.testing import ActivityEnvironment
    from tranibor_orchestrator.activities.tracker import reconcile_tracker

    result: dict[str, Any] = await ActivityEnvironment().run(reconcile_tracker, {"project_slug": "desk"})
    return result


async def _wait(predicate: Any, what: str, tries: int = 600) -> Any:
    for _ in range(tries):
        found = await predicate()
        if found:
            return found
        await asyncio.sleep(0.1)
    raise AssertionError(f"{what}: never")


async def test_a_mail_asking_for_the_crm_is_granted_and_answered_in_its_thread(
    platform: Platform, worker: Any, kube: Any, directory: Any
) -> None:
    from choregos_m365_desk.fake import MAIL_HTML, MAIL_PLAIN, message_id
    from tranibor_api.temporal import interpreter_id
    from tranibor_orchestrator.train_client import fake_starts

    client = platform.client
    await login(client)
    lea_id = directory.add_account(LEA)
    directory.groups[GROUP] = set()
    server = await _organisation(platform, kube)
    project = await _project(platform)
    fake = server.fake
    for seeded in (MAIL_PLAIN, MAIL_HTML):
        del fake.messages[seeded]  # HARNESS: one request in the inbox, ours
    mail_id = message_id(42)
    fake.add_message(
        mail_id, "CRM access", LEA, "Could I get the CRM please", received="2026-11-19T09:00:00Z"
    )
    key = f"mail:{mail_id}"

    tracker = await _use_the_project_tracker(platform)
    assert "agent-ready" in (await tracker.fetch_item(key)).labels
    passed = await _reconcile(platform)
    assert passed["created"] == [key] and passed["started"] == [key], passed
    item = await platform.work_item(key)
    assert item is not None, key
    start = fake_starts()[interpreter_id("desk", key)]

    async with worker():
        platform.adapters.executor.queue_result(
            StageResult(
                status=StageStatus.DONE,
                summary="CRM for lea",
                outputs={
                    "category": "access",
                    "summary": "Lea asks for the CRM; the catalogue opens app-crm-users.",
                    "plan": {
                        "grants": [
                            {"upn": LEA, "tool": "CRM", "group_id": GROUP, "group_name": "app-crm-users",
                             "sensitive": False, "approver_group": "crm", "reason": "Could I get the CRM please"}
                        ],
                        "bugs": [],
                    },
                },
            )
        )  # fmt: skip
        interpreter = await platform.env.client.start_workflow(
            "WorkflowInterpreter", start, id=interpreter_id("desk", key), task_queue="e2e"
        )

        async def child() -> Any:
            return await platform.work_item(f"{key}.1")

        born = await _wait(child, "the grant child is born")
        assert (born.documents or {}).get("parent"), born.documents

        async def pending() -> Any:
            listed = (
                await client.get(
                    f"/api/v1/projects/{project['id']}/actions", params={"status": "pending_approval"}
                )
            ).json()["items"]
            return [a for a in listed if a["kind"] == "access.grant"]

        (grant,) = await _wait(pending, "access.grant pending")
        assert grant["approval"]["approvers"] == [
            {"role": "approver", "group": "access-approvers-crm", "min": 1}
        ]
        assert grant["approval"]["excluded"] == [LEA, LEA], grant["approval"]
        path = f"/api/v1/projects/{project['id']}/actions/{grant['id']}/decision"
        lea = await _as(platform, LEA)
        try:
            refused = await lea.post(path, json={"decision": "approve"})
        finally:
            await lea.aclose()
        assert refused.status_code == 403, refused.text
        owner = await _as(platform, CRM_OWNER)
        try:
            approved = await owner.post(path, json={"decision": "approve"})
        finally:
            await owner.aclose()
        assert approved.status_code == 200 and approved.json()["status"] == "approved", approved.text

        async def in_group() -> bool:
            return lea_id in directory.groups.get(GROUP, set())

        await _wait(in_group, "lea in the CRM group")

        async def replied() -> Any:
            return fake.folder("sentitems")

        await _wait(replied, "a reply sent")
        result = await interpreter.result()
    assert result["state"] == "settled", result
    original = fake.messages[mail_id]
    (reply,) = fake.folder("sentitems")
    (mailed,) = fake.sent_mail
    assert mailed["to"] == [LEA] and reply.to == [LEA], mailed
    assert reply.conversation == (original.conversation or f"conv-{mail_id[-12:]}"), reply.conversation
    assert (
        "Done: lea@northwind.dev was added to the directory group 00000000-0000-0000-0000-000000000003"
        in (reply.body)
    ), reply.body
    assert "Choregos: Done" in original.categories, original.categories
    child_row = await platform.work_item(f"{key}.1")
    parent_row = await platform.work_item(key)
    assert (child_row.state, parent_row.state) == ("granted", "settled")
    assert child_row.fields["requester"] == LEA and child_row.fields["request"] == key, child_row.fields
    # A page since the core's ADR 0206 (graalsystems/tranibor 0.28.1): `items`, then `meta.next_cursor`.
    actions = (await client.get(f"/api/v1/projects/{project['id']}/actions")).json()["items"]
    assert sorted((a["kind"], a["status"]) for a in actions) == [
        ("access.grant", "succeeded"),
        ("desk.reply", "succeeded"),
    ]
    await server.close()


DESK_PERSON = "desk-person@northwind.dev"
HOOK = "/api/v1/webhooks/plugins/northwind/microsoft-365-desk/desk"


async def test_a_teams_mention_is_answered_by_a_desk_person_in_its_thread(
    platform: Platform, worker: Any, kube: Any, directory: Any
) -> None:
    from choregos_m365_desk.config import FAKE_CHANNEL_ID
    from choregos_m365_desk.fake import ROOT_ID, activity
    from tranibor_api.temporal import interpreter_id

    client = platform.client
    await login(client)
    server = await _organisation(platform, kube, teams=True)
    assert (await client.get(f"{ORG}/connectors/desk")).json()["status"] == "deployed"
    project = await _project(platform)
    await _member(client, DESK_PERSON, "approver", ["it-desk"])
    fake = server.fake
    await _use_the_project_tracker(platform)

    posted = activity("<at>Desk</at> Where do I find the expense policy?")
    body = json.dumps(posted).encode()
    unsigned = await client.post(HOOK, content=body, headers={"Content-Type": "application/json"})
    assert unsigned.status_code == 401, "a delivery without the bot service's token is no one's"
    token = fake.issuer.mint()
    answered = await client.post(
        HOOK, content=body, headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    )
    assert answered.status_code == 202, answered.text
    key = f"teams:{FAKE_CHANNEL_ID};messageid={ROOT_ID};{ROOT_ID}"
    item = await platform.work_item(key)
    assert item is not None, key

    async with worker():
        platform.adapters.executor.queue_result(
            StageResult(
                status=StageStatus.DONE,
                summary="a question",
                outputs={"category": "other", "summary": "A question about the expense policy.",
                         "plan": {"grants": [], "bugs": []}},
            )
        )  # fmt: skip
        interpreter = await platform.env.client.start_workflow(
            "WorkflowInterpreter",
            {
                "project_id": project["id"],
                "project_slug": "desk",
                "work_item_id": item.id,
                "tracker_key": key,
            },
            id=interpreter_id("desk", key),
            task_queue="e2e",
        )

        async def request() -> Any:
            read = (await client.get(f"/api/v1/work-items/{item.id}")).json()
            return read.get("pending_request")

        pending = await _wait(request, "the desk's task")
        assert pending["kind"] == "task" and "answer" in pending["payload"]["form"]["properties"], pending
        person = await _as(platform, DESK_PERSON)
        try:
            done = await person.post(
                f"/api/v1/work-items/{item.id}/decisions",
                json={
                    "kind": "complete",
                    "values": {"answer": "The policy is on the intranet, page Expenses."},
                },
            )
        finally:
            await person.aclose()
        assert done.status_code == 202, done.text
        result = await interpreter.result()
    assert result["state"] == "handled", result
    (sent,) = fake.connector_replies
    assert sent["conversation"] == f"{FAKE_CHANNEL_ID};messageid={ROOT_ID}" and sent["reply_to"] == ROOT_ID, (
        sent
    )
    assert "The policy is on the intranet, page Expenses." in sent["activity"]["text"], sent
    # A page since the core's ADR 0206 (graalsystems/tranibor 0.28.1): `items`, then `meta.next_cursor`.
    actions = (await client.get(f"/api/v1/projects/{project['id']}/actions")).json()["items"]
    assert [(a["kind"], a["status"]) for a in actions] == [("desk.answer", "succeeded")], actions
    await server.close()
