# SPDX-License-Identifier: Apache-2.0
"""Hand-written exchanges with an Odoo, played by `respx`: no network, invented data.

A test scripts the calls it expects, in order (`expect`), and what Odoo answers. A call that is not the next
one expected fails the test where it happens, naming both; a call left unplayed fails it at the end. Every
request is kept (`calls`) so a test can also read what was sent: its headers, its domain, its values.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import respx

from tranibor_purchasing.backends.odoo import OdooPurchasing
from tranibor_purchasing.config import Settings

BASE = "https://odoo.example.test"
KEY = "invented-api-key-0123456789abcdef"
ODOO_ENV = {
    "PURCHASING_BACKEND": "odoo",
    "PURCHASING_BASE_URL": BASE,
    "PURCHASING_DATABASE": "acme",
    "PURCHASING_USERNAME": "purchasing-bot",
    "PURCHASING_API_KEY": KEY,
    "CHOREGOS_SERVER_TOKEN": "platform-token-for-tests",
}


@dataclass
class Call:
    model: str
    method: str
    body: dict[str, Any]
    headers: httpx.Headers


@dataclass
class _Expected:
    model: str
    method: str
    returns: Any
    status: int
    check: Callable[[dict[str, Any]], None] | None


class Script:
    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._queue: list[_Expected] = []

    def expect(
        self,
        model: str,
        method: str,
        returns: Any = None,
        *,
        status: int = 200,
        check: Callable[[dict[str, Any]], None] | None = None,
    ) -> Script:
        self._queue.append(_Expected(model, method, returns, status, check))
        return self

    def handle(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.split("/")
        assert parts[:3] == ["", "json", "2"] and len(parts) == 5, f"not a JSON-2 call: {request.url.path}"
        model, method = parts[3], parts[4]
        body = json.loads(request.content)
        self.calls.append(Call(model, method, body, request.headers))
        assert self._queue, f"unexpected call {model}.{method} {body}: nothing more was scripted"
        expected = self._queue.pop(0)
        assert (model, method) == (expected.model, expected.method), (
            f"expected {expected.model}.{expected.method}, got {model}.{method} {body}"
        )
        if expected.check is not None:
            expected.check(body)
        return httpx.Response(expected.status, json=expected.returns)

    def done(self) -> None:
        assert not self._queue, f"scripted but never called: {[f'{e.model}.{e.method}' for e in self._queue]}"

    def sent(self, model: str, method: str) -> list[dict[str, Any]]:
        return [c.body for c in self.calls if (c.model, c.method) == (model, method)]


@dataclass
class Odoo:
    backend: OdooPurchasing
    script: Script


def has_registry(script: Script) -> Script:
    """The one question the backend asks Odoo about its version: an Odoo 19, which has `company_registry`."""
    return script.expect("res.partner", "fields_get", {"company_registry": {"type": "char"}})


def no_registry(script: Script) -> Script:
    """An Odoo 20: `fields_get` knows no `company_registry` on a partner."""
    return script.expect("res.partner", "fields_get", {})


@pytest.fixture
def script() -> Iterator[Script]:
    played = Script()
    with respx.mock(assert_all_called=False) as router:
        router.route(host="odoo.example.test").mock(side_effect=played.handle)
        yield played
    played.done()


@pytest.fixture
def odoo(script: Script) -> Odoo:
    settings = Settings.from_env(ODOO_ENV)
    return Odoo(OdooPurchasing.from_settings(settings), script)


def one_supplier_row(number: int = 7, **more: Any) -> dict[str, Any]:
    """A `res.partner` row as Odoo's `search_read` answers it (Odoo 19)."""
    return {
        "id": number,
        "name": "Brightwater Office Supplies Ltd",
        "vat": "GB999000101",
        "country_code": "GB",
        "email": "sales@brightwater.example",
        "phone": False,
        "website": False,
        "street": "1 Example Row",
        "city": "Leeds",
        "zip": "LS1 0AA",
        "category_id": [3],
        "active": True,
        "create_date": "2026-11-20 09:00:00",
        "write_date": "2026-11-20 09:30:00",
        "company_registry": "0999 0101",
        **more,
    }
