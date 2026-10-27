# SPDX-License-Identifier: Apache-2.0
"""The ServiceNow Table API (`/api/now/table/<table>`), behind the rules of this server.

- Authentication: OAuth 2.0 client credentials (`/oauth_token.do`, the token cached until shortly before
  it expires, renewed once on a 401) or basic authentication of a dedicated integration user.
- Every read names its fields (`sysparm_fields`) and asks for `sysparm_display_value=all`, so a
  reference comes back with both its sys_id and its label; at most `MAX_ROWS` rows.
- Only the tables of `tables.py`; a query is always built from typed filters (`query.py`).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from .config import Settings
from .errors import ServiceNowError
from .query import Query, eq
from .tables import TABLE_FIELDS, check_fields, check_sys_id, check_table

MAX_ROWS = 50
#: A token is renewed this many seconds before ServiceNow says it expires.
TOKEN_MARGIN_S = 60
#: How long the state labels read from `sys_choice` are kept.
CHOICES_TTL_S = 3600

log = structlog.get_logger("choregos_servicenow.client")


@dataclass
class _Token:
    value: str
    expires_at: float


def normalise(table: str, raw: dict[str, Any]) -> dict[str, Any]:
    """A record as the tools hand it out: `{table, sys_id, fields, display}`.

    `fields` holds the stored values (sys_ids for references, codes for choices); `display` holds the
    label of each field whose label differs from its value (a group's name, a state's label)."""
    values: dict[str, Any] = {}
    display: dict[str, Any] = {}
    for name, cell in raw.items():
        if isinstance(cell, dict) and ("value" in cell or "display_value" in cell):
            value = cell.get("value")
            label = cell.get("display_value")
        else:
            value = label = cell
        values[name] = value
        if label not in (None, "") and label != value:
            display[name] = label
    return {"table": table, "sys_id": values.get("sys_id"), "fields": values, "display": display}


class ServiceNowClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        auth: httpx.Auth | None = None
        if settings.auth == "basic":
            assert settings.username is not None and settings.password is not None
            auth = httpx.BasicAuth(settings.username, settings.password.get_secret_value())
        self._http = httpx.AsyncClient(
            base_url=settings.instance_url,
            auth=auth,
            transport=transport,
            timeout=httpx.Timeout(30.0),
            headers={"Accept": "application/json"},
        )
        self._token: _Token | None = None
        self._token_lock = asyncio.Lock()
        self._choices: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}

    @property
    def settings(self) -> Settings:
        return self._settings

    async def aclose(self) -> None:
        await self._http.aclose()

    # ─────────────────────────── authentication ───────────────────────────

    async def _bearer(self, renew: bool = False) -> str:
        async with self._token_lock:
            now = time.monotonic()
            if not renew and self._token is not None and now < self._token.expires_at:
                return self._token.value
            s = self._settings
            assert s.client_id is not None and s.client_secret is not None
            response = await self._http.post(
                "/oauth_token.do",
                data={
                    "grant_type": "client_credentials",
                    "client_id": s.client_id,
                    "client_secret": s.client_secret.get_secret_value(),
                },
            )
            if response.status_code != 200:
                raise ServiceNowError(f"OAuth token refused ({response.status_code})", response.status_code)
            body = response.json()
            expires_in = int(body.get("expires_in", 1800))
            self._token = _Token(str(body["access_token"]), now + max(expires_in - TOKEN_MARGIN_S, 0))
            log.info("oauth_token_obtained", expires_in=expires_in)
            return self._token.value

    async def _request(self, method: str, path: str, *, params: dict[str, str] | None = None, json: Any = None) -> Any:
        for attempt in (0, 1):
            headers: dict[str, str] = {}
            if self._settings.auth == "oauth":
                headers["Authorization"] = f"Bearer {await self._bearer(renew=attempt == 1)}"
            try:
                response = await self._http.request(method, path, params=params, json=json, headers=headers)
            except httpx.HTTPError as error:
                raise ServiceNowError(f"ServiceNow unreachable: {type(error).__name__}") from error
            if response.status_code == 401 and self._settings.auth == "oauth" and attempt == 0:
                log.info("oauth_token_rejected_renewing")
                continue
            if response.status_code >= 400:
                raise ServiceNowError(_error_message(response), response.status_code)
            return response.json().get("result") if response.content else None
        raise ServiceNowError("ServiceNow refused the renewed token", 401)  # pragma: no cover

    # ─────────────────────────── table access ───────────────────────────

    async def list_records(
        self,
        table: str,
        query: Query,
        *,
        fields: list[str] | None = None,
        limit: int = MAX_ROWS,
    ) -> list[dict[str, Any]]:
        check_table(table)
        assert query.table == table
        params = {
            "sysparm_query": query.encode(),
            "sysparm_fields": ",".join(check_fields(table, fields)),
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": str(max(1, min(limit, MAX_ROWS))),
        }
        result = await self._request("GET", f"/api/now/table/{table}", params=params)
        log.info("table_read", table=table, rows=len(result or []))
        return [normalise(table, row) for row in result or []]

    async def get(self, table: str, sys_id: str, *, fields: list[str] | None = None) -> dict[str, Any]:
        check_table(table)
        check_sys_id(sys_id)
        params = {
            "sysparm_fields": ",".join(check_fields(table, fields)),
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        }
        result = await self._request("GET", f"/api/now/table/{table}/{sys_id}", params=params)
        return normalise(table, result or {})

    async def find_one(self, table: str, query: Query) -> dict[str, Any] | None:
        rows = await self.list_records(table, query, limit=1)
        return rows[0] if rows else None

    async def create(self, table: str, values: dict[str, Any]) -> dict[str, Any]:
        check_table(table)
        params = {
            "sysparm_fields": ",".join(TABLE_FIELDS[table]),
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        }
        result = await self._request("POST", f"/api/now/table/{table}", params=params, json=values)
        record = normalise(table, result or {})
        log.info("record_created", table=table, sys_id=record["sys_id"])
        return record

    async def update(self, table: str, sys_id: str, values: dict[str, Any]) -> dict[str, Any]:
        check_table(table)
        check_sys_id(sys_id)
        params = {
            "sysparm_fields": ",".join(TABLE_FIELDS[table]),
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
        }
        result = await self._request("PATCH", f"/api/now/table/{table}/{sys_id}", params=params, json=values)
        log.info("record_updated", table=table, sys_id=sys_id, fields=sorted(values))
        return normalise(table, result or {})

    # ─────────────────────────── choices ───────────────────────────

    async def choices(self, table: str, element: str = "state") -> dict[str, str]:
        """`{value: label}` of a choice field, read from `sys_choice` and cached."""
        key = (table, element)
        cached = self._choices.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < CHOICES_TTL_S:
            return cached[1]
        q = Query("sys_choice").where(eq("name", table), eq("element", element), eq("inactive", "false"))
        q.order("sequence")
        rows = await self.list_records("sys_choice", q, fields=["value", "label", "language"])
        mapping: dict[str, str] = {}
        for row in rows:
            language = row["fields"].get("language")
            if language not in (None, "", "en"):
                continue
            mapping[str(row["fields"]["value"])] = str(row["fields"]["label"])
        self._choices[key] = (now, mapping)
        return mapping

    async def state_value(self, table: str, *labels: str) -> str | None:
        """The stored value of the first state whose label is one of `labels` (case-insensitive)."""
        mapping = await self.choices(table)
        by_label = {label.lower(): value for value, label in mapping.items()}
        for label in labels:
            if label.lower() in by_label:
                return by_label[label.lower()]
        return None


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error") or {}
        detail = error.get("message") or error.get("detail") or ""
    except ValueError:
        detail = ""
    return f"ServiceNow answered {response.status_code}" + (f": {detail}" if detail else "")
