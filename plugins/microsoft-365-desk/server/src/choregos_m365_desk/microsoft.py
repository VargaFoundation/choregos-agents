# SPDX-License-Identifier: Apache-2.0
"""The three Microsoft endpoints the server calls, with the application's own credentials.

- **Entra** (`login.microsoftonline.com`): client-credentials tokens, one per scope, cached until shortly
  before they expire and renewed once when refused.
- **Microsoft Graph** (`graph.microsoft.com/v1.0`): every URL is checked to be under Graph before the
  token goes with it, so a next page (`@odata.nextLink`) elsewhere is refused; no redirect is followed. A
  throttled call (429, 503) is retried at most twice, after `Retry-After` when it is at most 10 seconds.
- **The Bot Connector** (the Teams service URL, `smba.trafficmanager.net`): the bot's token is sent only to
  an allowed service URL (`config.allowed_service_url`).

Neither a token nor the client secret nor a body Microsoft returned is ever logged or put in an error:
an error carries the HTTP status and Microsoft's own error code only.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from .config import BOT_SCOPE, GRAPH_SCOPE, GRAPH_URL, LOGIN_URL, Settings, allowed_service_url
from .errors import Failure, Refused

TIMEOUT_S = 15.0
#: A token is renewed this many seconds before Entra says it expires.
TOKEN_MARGIN_S = 120
MAX_RETRIES = 2
MAX_RETRY_AFTER_S = 10.0

log = structlog.get_logger("choregos_m365_desk.microsoft")

Sleep = Callable[[float], Awaitable[None]]


def segment(value: str) -> str:
    """One path segment, percent-encoded entirely (ids may hold `/`, `+`, `:`, `@`, `;`, `=`)."""
    return quote(value, safe="")


@dataclass
class _Token:
    value: str
    expires_at: float


class Tokens:
    """Client-credentials tokens of the application, per (tenant, client, scope)."""

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings
        self._cache: dict[tuple[str, str, str], _Token] = {}
        self._lock = asyncio.Lock()

    async def token(self, tenant: str, client_id: str, scope: str, *, renew: bool = False) -> str:
        key = (tenant, client_id, scope)
        async with self._lock:
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached is not None and not renew and now < cached.expires_at:
                return cached.value
            try:
                response = await self.http.post(
                    f"{LOGIN_URL}/{segment(tenant)}/oauth2/v2.0/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": self.settings.client_secret.get_secret_value(),
                        "scope": scope,
                    },
                )
            except httpx.HTTPError as error:
                raise Failure("upstream_error", f"Entra could not be reached ({type(error).__name__})") from None
            if response.status_code != 200:
                raise Failure(
                    "unauthorized",
                    f"Entra refused the application's credentials for {scope} ({_entra_code(response)})",
                    response.status_code,
                )
            body = response.json()
            expires_in = int(body.get("expires_in", 3600))
            self._cache[key] = _Token(str(body["access_token"]), now + max(expires_in - TOKEN_MARGIN_S, 0))
            log.info("token_obtained", scope=scope, expires_in=expires_in)
            return self._cache[key].value

    async def graph(self, *, renew: bool = False) -> str:
        s = self.settings
        return await self.token(s.tenant_id, s.client_id, GRAPH_SCOPE, renew=renew)

    async def bot(self, *, renew: bool = False) -> str:
        s = self.settings
        return await self.token(s.bot_tenant_id, s.bot_id, BOT_SCOPE, renew=renew)


class Graph:
    def __init__(self, http: httpx.AsyncClient, tokens: Tokens, sleep: Sleep = asyncio.sleep) -> None:
        self.http = http
        self.tokens = tokens
        self.sleep = sleep

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """One call; `path` is under Graph (`/users/…`) or a whole Graph URL (a next page)."""
        url = path if path.startswith("https://") else f"{GRAPH_URL}{path}"
        if not url.startswith(GRAPH_URL + "/"):
            raise Failure("upstream_error", "a Microsoft Graph URL outside Graph: refused")
        renewed = False
        retries = 0
        while True:
            token = await self.tokens.graph(renew=renewed)
            sent = {"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})}
            try:
                response = await self.http.request(method, url, params=params, json=json, headers=sent)
            except httpx.HTTPError as error:
                raise Failure(
                    "upstream_error", f"Microsoft Graph could not be reached ({type(error).__name__})"
                ) from None
            if response.status_code == 401 and not renewed:
                renewed = True
                continue
            if response.status_code in (429, 503) and retries < MAX_RETRIES:
                wait = _retry_after(response)
                if wait is not None:
                    retries += 1
                    log.info("graph_throttled", status=response.status_code, wait_s=wait, attempt=retries)
                    await self.sleep(wait)
                    continue
            if 200 <= response.status_code < 300:
                if not response.content:
                    return {}
                try:
                    body = response.json()
                except ValueError:
                    raise Failure("upstream_error", "Microsoft Graph answered something that is not JSON") from None
                if not isinstance(body, dict):
                    raise Failure("upstream_error", "Microsoft Graph answered an unexpected shape")
                return body
            raise _failure("Microsoft Graph", response)

    async def pages(
        self,
        path: str,
        params: Mapping[str, str],
        *,
        max_pages: int,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """The `value` of each page, at most `max_pages` of them, following `@odata.nextLink` within Graph."""
        url: str = path
        query: Mapping[str, str] | None = params
        for _ in range(max_pages):
            body = await self.request("GET", url, params=query, headers=headers)
            value = body.get("value")
            yield [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
            next_link = body.get("@odata.nextLink")
            if not isinstance(next_link, str) or not next_link:
                return
            if not next_link.startswith(GRAPH_URL + "/"):
                raise Failure("upstream_error", "Microsoft Graph named a next page outside Graph: refused")
            url, query = next_link, None


class Connector:
    """The Bot Connector REST API: a reply in a conversation's thread."""

    def __init__(self, http: httpx.AsyncClient, tokens: Tokens) -> None:
        self.http = http
        self.tokens = tokens

    async def reply(
        self, service_url: str, conversation_id: str, activity_id: str, activity: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        """`POST {service_url}/v3/conversations/{conversation}/activities/{activity}`: the status and the
        answer (`{"id": …}`). A 409 (the activity already exists) is answered, not raised."""
        if not allowed_service_url(service_url):
            raise Refused("the Bot Connector's service URL is not an allowed host: nothing was sent")
        url = f"{service_url.rstrip('/')}/v3/conversations/{segment(conversation_id)}/activities/{segment(activity_id)}"
        renewed = False
        while True:
            token = await self.tokens.bot(renew=renewed)
            try:
                response = await self.http.post(url, json=activity, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as error:
                raise Failure(
                    "upstream_error", f"the Bot Connector could not be reached ({type(error).__name__})"
                ) from None
            if response.status_code == 401 and not renewed:
                renewed = True
                continue
            if response.status_code in (200, 201, 202, 409):
                try:
                    body = response.json() if response.content else {}
                except ValueError:
                    body = {}
                return response.status_code, body if isinstance(body, dict) else {}
            raise _failure("the Bot Connector", response)


def _retry_after(response: httpx.Response) -> float | None:
    """How long to wait before retrying, when Microsoft says and it is short enough; else `None`."""
    raw = response.headers.get("Retry-After", "")
    try:
        wait = float(raw) if raw else 1.0
    except ValueError:
        return None
    return wait if 0 <= wait <= MAX_RETRY_AFTER_S else None


def _entra_code(response: httpx.Response) -> str:
    try:
        code = response.json().get("error")
    except (ValueError, AttributeError):
        return "unknown"
    return code if isinstance(code, str) and code.isascii() and len(code) <= 80 else "unknown"


def _graph_code(response: httpx.Response) -> str:
    """Microsoft's own error code (`ErrorAccessDenied`…), never its message, which may quote data."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return "unknown"
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code.isascii() and len(code) <= 80 else "unknown"


def _failure(who: str, response: httpx.Response) -> Failure:
    status, code = response.status_code, _graph_code(response)
    if 300 <= status < 400:
        return Failure("upstream_error", f"{who} answered a redirect ({status}): not followed", status)
    if status == 401:
        return Failure("unauthorized", f"{who} refused the application's token ({code})", status)
    if status == 403:
        return Failure(
            "forbidden",
            f"{who} refused the application ({code}): a permission, a consent or the mailbox's RBAC scope",
            status,
        )
    if status == 404:
        return Failure("not_found", f"{who} found nothing there ({code})", status)
    if status == 429:
        retry = response.headers.get("Retry-After", "")
        after = f", retry after {retry} s" if retry.isdigit() else ""
        return Failure("rate_limited", f"{who} throttled the call ({code}){after}", status)
    return Failure("upstream_error", f"{who} answered {status} ({code})", status)
