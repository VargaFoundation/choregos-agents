# SPDX-License-Identifier: Apache-2.0
"""Inbound events: what a ServiceNow Business Rule posts, verified and normalised.

The Business Rule (shipped in the plugin's procedure) posts, for each insert or update of an incident,
a request item or a change, a JSON object (or a list of them):

    {"table": "incident", "sys_id": "<32 hex>", "number": "INC0010001",
     "sys_mod_count": 3, "operation": "insert" | "update" | "comment", "state": "2"}

with the shared secret in `X-Choregos-Secret`. Each becomes `{kind, item_key, delivery_id}` where
`delivery_id = sys_id:sys_mod_count` — a replayed delivery carries the same id, and the core de-duplicates
on it. The core re-reads the item before acting, so nothing else of the payload is trusted.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from typing import Any

from .errors import Refused
from .tables import TRACKER_TABLES, is_sys_id, table_of_number

SECRET_HEADER = "x-choregos-secret"  # noqa: S105 - a header name

CREATED = "tracker.item.created"
UPDATED = "tracker.item.updated"
COMMENTED = "tracker.item.commented"
CLOSED = "tracker.item.closed"

#: The stored state values that close an item, per table (ServiceNow's defaults).
CLOSED_STATES: dict[str, frozenset[str]] = {
    "incident": frozenset({"6", "7", "8"}),
    "sc_req_item": frozenset({"3", "4", "7"}),
    "change_request": frozenset({"3", "4"}),
}


def verify(headers: Mapping[str, str], secret: str | None) -> bool:
    """Constant-time comparison of `X-Choregos-Secret` with the configured secret."""
    if not secret:
        return False
    lowered = {k.lower(): v for k, v in headers.items()}
    given = lowered.get(SECRET_HEADER, "")
    return hmac.compare_digest(given.encode(), secret.encode())


def parse(body: bytes) -> list[dict[str, str]]:
    try:
        payload: Any = json.loads(body or b"null")
    except ValueError as error:
        raise Refused("the body is not JSON") from error
    items = payload if isinstance(payload, list) else [payload]
    events: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            raise Refused("each event is a JSON object")
        event = _event(item)
        if event is not None:
            events.append(event)
    return events


def _event(item: dict[str, Any]) -> dict[str, str] | None:
    table = str(item.get("table", ""))
    if table not in TRACKER_TABLES:
        return None
    sys_id = str(item.get("sys_id", ""))
    number = str(item.get("number", ""))
    if not is_sys_id(sys_id):
        raise Refused("an event needs the record's sys_id")
    if table_of_number(number) != table:
        raise Refused(f"{number} is not a {table} number")
    try:
        mod_count = int(item.get("sys_mod_count", -1))
    except (TypeError, ValueError) as error:
        raise Refused("sys_mod_count is an integer") from error
    if mod_count < 0:
        raise Refused("an event needs the record's sys_mod_count")
    operation = str(item.get("operation", "update"))
    if operation == "insert":
        kind = CREATED
    elif operation == "comment":
        kind = COMMENTED
    elif operation == "update":
        kind = CLOSED if str(item.get("state", "")) in CLOSED_STATES[table] else UPDATED
    else:
        return None
    return {"kind": kind, "item_key": number, "delivery_id": f"{sys_id}:{mod_count}"}
