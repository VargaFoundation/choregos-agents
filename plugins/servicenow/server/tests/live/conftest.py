# SPDX-License-Identifier: Apache-2.0
"""The server against a REAL ServiceNow instance: opt-in, skipped without its variables.

    TRANIBOR_LIVE_SERVICENOW_URL=https://<instance>.service-now.com \\
    TRANIBOR_LIVE_SERVICENOW_USERNAME=choregos.integration TRANIBOR_LIVE_SERVICENOW_PASSWORD=… \\
    TRANIBOR_LIVE_SERVICENOW_CLIENT_ID=… TRANIBOR_LIVE_SERVICENOW_CLIENT_SECRET=… \\
    TRANIBOR_LIVE_SERVICENOW_CLEANUP_USERNAME=… TRANIBOR_LIVE_SERVICENOW_CLEANUP_PASSWORD=… \\
      uv run pytest tests/live -q

- The username and password are the integration user's (basic authentication: on Australia and later it
  needs the role `snc_basic_auth_api_access`); the client id and secret, its OAuth client credentials
  integration. Each set that is present runs every test; at least one is needed.
- The cleanup user may delete (an administrator of a development instance): the run deletes what it
  created, and files the request item the tracker tests need. Without it, the run leaves its records
  resolved or canceled, their titles tagged `[choregos-live <run>]`, and the request item test skips.
- `TRANIBOR_LIVE_SERVICENOW_GROUP` is the assignment group of the queue (`Service Desk` by default).

`TRANIBOR_LIVE_REQUIRED=servicenow` turns a missing variable into a failure, as the core's live suite does.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest

from choregos_servicenow.app import Runtime, build_server
from choregos_servicenow.config import Settings

from ..conftest import Mcp

RUN = uuid.uuid4().hex[:8]
TAG = f"[choregos-live {RUN}]"


def _env(name: str) -> str:
    return os.environ.get(f"TRANIBOR_LIVE_SERVICENOW_{name}", "")


def _required() -> bool:
    wanted = {w.strip().lower() for w in os.environ.get("TRANIBOR_LIVE_REQUIRED", "").split(",")}
    return bool(wanted & {"servicenow", "all"})


def _missing(what: str) -> None:
    if _required():
        pytest.fail(f"live ServiceNow test required but not configured: {what}", pytrace=False)
    pytest.skip(f"live ServiceNow test skipped: {what}")


def modes() -> list[str]:
    found = []
    if _env("USERNAME") and _env("PASSWORD"):
        found.append("basic")
    if _env("CLIENT_ID") and _env("CLIENT_SECRET"):
        found.append("oauth")
    return found or ["unconfigured"]


def live_env(mode: str, **overrides: str) -> dict[str, str]:
    if not _env("URL") or mode == "unconfigured":
        _missing("TRANIBOR_LIVE_SERVICENOW_URL and a username/password or a client id/secret")
    env = {
        "SERVICENOW_BASE_URL": _env("URL"),
        "SERVICENOW_AUTH": mode,
        "SERVICENOW_ASSIGNMENT_GROUPS": _env("GROUP") or "Service Desk",
    }
    if mode == "basic":
        env |= {"SERVICENOW_USERNAME": _env("USERNAME"), "SERVICENOW_PASSWORD": _env("PASSWORD")}
    else:
        env |= {"SERVICENOW_CLIENT_ID": _env("CLIENT_ID"), "SERVICENOW_CLIENT_SECRET": _env("CLIENT_SECRET")}
    return env | overrides


@pytest.fixture(params=modes())
def mode(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
async def runtime(mode: str) -> AsyncIterator[Runtime]:
    """The server's own runtime on the real instance (one per test: httpx binds a loop)."""
    rt = Runtime(Settings.from_env(live_env(mode)))
    try:
        yield rt
    finally:
        await rt.client.aclose()


class LiveMcp(Mcp):
    def __init__(self, runtime: Runtime, mode: str) -> None:
        super().__init__(build_server(runtime))
        self.mode = mode


@pytest.fixture
def mcp(runtime: Runtime, mode: str) -> LiveMcp:
    """Every call goes through the MCP protocol, as the platform makes it."""
    return LiveMcp(runtime, mode)


class Admin:
    """The cleanup user, straight on the Table API: files fixtures and deletes what the run created."""

    def __init__(self) -> None:
        self.http = httpx.Client(
            base_url=_env("URL").rstrip("/"),
            auth=(_env("CLEANUP_USERNAME"), _env("CLEANUP_PASSWORD")),
            headers={"Accept": "application/json"},
            timeout=60,
        )
        self.created: list[tuple[str, str]] = []

    def create(self, table: str, values: dict[str, Any]) -> dict[str, Any]:
        response = self.http.post(f"/api/now/table/{table}", json=values)
        response.raise_for_status()
        record: dict[str, Any] = response.json()["result"]
        self.created.append((table, record["sys_id"]))
        return record

    def rows(self, table: str, query: str, fields: str) -> list[dict[str, Any]]:
        response = self.http.get(
            f"/api/now/table/{table}", params={"sysparm_query": query, "sysparm_fields": fields, "sysparm_limit": "200"}
        )
        response.raise_for_status()
        rows: list[dict[str, Any]] = response.json()["result"]
        return rows

    def delete_run(self) -> None:
        """Every record whose title carries this run's tag, and the fixtures filed for it."""
        for table in ("incident", "change_request", "sc_req_item"):
            for row in self.rows(table, f"short_descriptionSTARTSWITH{TAG}", "sys_id"):
                self.created.append((table, row["sys_id"]))
        for table, sys_id in dict.fromkeys(self.created):
            self.http.delete(f"/api/now/table/{table}/{sys_id}")


@pytest.fixture(scope="session")
def admin() -> Iterator[Admin | None]:
    if not (_env("URL") and _env("CLEANUP_USERNAME") and _env("CLEANUP_PASSWORD")):
        yield None
        return
    helper = Admin()
    try:
        yield helper
    finally:
        helper.delete_run()
        helper.http.close()
