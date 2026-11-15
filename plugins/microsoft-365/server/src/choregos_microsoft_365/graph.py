# SPDX-License-Identifier: Apache-2.0
"""A thin read-only client of Microsoft Graph, called with the person's token for one call.

The token is sent to `GRAPH_URL` only: a next page (`@odata.nextLink`) outside it is refused, so a
response can never steer the person's token to another host. No redirect is followed. Nothing is
cached or stored, and neither the token nor a response body is ever logged or put in an error.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from .config import GRAPH_URL
from .errors import Failure

TIMEOUT_S = 15.0


class Graph:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, base_url: str = GRAPH_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, timeout=TIMEOUT_S, follow_redirects=False)

    async def get(
        self, token: str, path: str, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        async with self._client() as client:
            return await self._get(client, token, f"{self.base_url}{path}", params, headers)

    async def pages(
        self,
        token: str,
        path: str,
        params: Mapping[str, str],
        *,
        max_pages: int,
        headers: Mapping[str, str] | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """The `value` of each page, at most `max_pages` of them, following `@odata.nextLink` within Graph."""
        async with self._client() as client:
            url: str | None = f"{self.base_url}{path}"
            query: Mapping[str, str] | None = params
            for _ in range(max_pages):
                assert url is not None
                body = await self._get(client, token, url, query, headers)
                value = body.get("value")
                yield [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
                url, query = body.get("@odata.nextLink"), None
                if not isinstance(url, str) or not url:
                    return
                if not url.startswith(self.base_url + "/"):
                    raise Failure("upstream_error", "Microsoft Graph named a next page outside Graph: refused")

    async def _get(
        self,
        client: httpx.AsyncClient,
        token: str,
        url: str,
        params: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        sent = {"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})}
        try:
            response = await client.get(url, params=params, headers=sent)
        except httpx.HTTPError as error:
            raise Failure("upstream_error", f"Microsoft Graph could not be reached ({type(error).__name__})") from None
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                raise Failure("upstream_error", "Microsoft Graph answered something that is not JSON", 200) from None
            if not isinstance(body, dict):
                raise Failure("upstream_error", "Microsoft Graph answered an unexpected shape", 200)
            return body
        raise _failure(response)


def _graph_code(response: httpx.Response) -> str:
    """Graph's own error code (`ErrorAccessDenied`…), never its message, which may quote data."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return "unknown"
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code.isascii() and len(code) <= 80 else "unknown"


def _failure(response: httpx.Response) -> Failure:
    status, code = response.status_code, _graph_code(response)
    if status == 401:
        return Failure(
            "identity_unavailable",
            f"Microsoft Graph refused the person's token ({code}): they may need to connect their account again",
            status,
        )
    if status == 403:
        return Failure(
            "forbidden",
            f"Microsoft Graph refused the person ({code}): the permission may not be granted to the application",
            status,
        )
    if status == 404:
        return Failure("not_found", f"Microsoft Graph found nothing there ({code})", status)
    if status == 429:
        retry = response.headers.get("Retry-After", "")
        after = f", retry after {retry} s" if retry.isdigit() else ""
        return Failure("rate_limited", f"Microsoft Graph throttled the call ({code}){after}", status)
    return Failure("upstream_error", f"Microsoft Graph answered {status} ({code})", status)
