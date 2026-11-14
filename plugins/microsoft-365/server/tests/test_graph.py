# SPDX-License-Identifier: Apache-2.0
"""The Graph client: what Graph answers becomes a typed error; the person's token never leaves Graph."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from choregos_microsoft_365.errors import Failure
from choregos_microsoft_365.graph import Graph

TOKEN = "the-persons-token"


def _graph(handler: Any) -> tuple[Graph, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        response = handler(request)
        assert isinstance(response, httpx.Response)
        return response

    return Graph(httpx.MockTransport(record)), seen


@pytest.mark.parametrize(
    ("status", "code", "kind"),
    [
        (401, "InvalidAuthenticationToken", "identity_unavailable"),
        (403, "ErrorAccessDenied", "forbidden"),
        (404, "ErrorItemNotFound", "not_found"),
        (429, "TooManyRequests", "rate_limited"),
        (500, "generalException", "upstream_error"),
        (503, "serviceNotAvailable", "upstream_error"),
    ],
)
async def test_graph_errors_are_typed_and_quote_no_body(status: int, code: str, kind: str) -> None:
    body = {"error": {"code": code, "message": f"a message quoting data and {TOKEN}"}}
    graph, _ = _graph(lambda _: httpx.Response(status, json=body, headers={"Retry-After": "7"}))
    with pytest.raises(Failure) as raised:
        await graph.get(TOKEN, "/me/messages")
    assert raised.value.code == kind
    assert code in str(raised.value)
    assert TOKEN not in str(raised.value) and "quoting" not in str(raised.value)
    if kind == "rate_limited":
        assert "retry after 7 s" in str(raised.value)


async def test_an_unreachable_graph_is_an_upstream_error() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(Failure) as raised:
        await Graph(httpx.MockTransport(down)).get(TOKEN, "/me/messages")
    assert raised.value.code == "upstream_error"


async def test_a_next_page_outside_graph_is_refused_before_the_token_is_sent() -> None:
    def pages(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [{"a": 1}], "@odata.nextLink": "https://evil.example/v1.0/me/x"})

    graph, seen = _graph(pages)
    collected: list[list[dict[str, Any]]] = []
    with pytest.raises(Failure, match="outside Graph"):
        async for page in graph.pages(TOKEN, "/me/messages", {"$top": "1"}, max_pages=3):
            collected.append(page)
    assert collected == [[{"a": 1}]]
    assert [str(r.url.host) for r in seen] == ["graph.microsoft.com"]


async def test_a_lookalike_host_is_not_graph() -> None:
    def pages(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [], "@odata.nextLink": "https://graph.microsoft.com/v1.0.evil/x"})

    graph, seen = _graph(pages)
    with pytest.raises(Failure, match="outside Graph"):
        async for _ in graph.pages(TOKEN, "/me/messages", {}, max_pages=3):
            pass
    assert len(seen) == 1


async def test_pages_stop_at_their_bound() -> None:
    def endless(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": [{}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/x?p=2"})

    graph, seen = _graph(endless)
    async for _ in graph.pages(TOKEN, "/me/messages", {}, max_pages=3):
        pass
    assert len(seen) == 3


async def test_a_redirect_is_not_followed() -> None:
    graph, seen = _graph(lambda _: httpx.Response(302, headers={"Location": "https://evil.example/"}))
    with pytest.raises(Failure) as raised:
        await graph.get(TOKEN, "/me/messages")
    assert raised.value.code == "upstream_error"
    assert len(seen) == 1
