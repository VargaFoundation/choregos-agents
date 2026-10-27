# SPDX-License-Identifier: Apache-2.0
"""What each tool does, independent of MCP (the server in `app.py` only exposes these methods).

Reads are bounded (`MAX_ROWS`) and name their fields. Writes are idempotent — replaying the same call
leaves one effect — and return what a compensation needs to undo them:

- `add_work_note` leaves a marker `[choregos:<key>]` in the note and does nothing if a note with the
  marker exists (the key is the platform's `action_key`, or a hash of the text);
- `add_comment` does nothing if the same comment is already on the record;
- `update_record` returns the previous values (its compensation writes them back); `priority` is never
  written: ServiceNow computes it from impact and urgency;
- `resolve_incident` returns the previous state (its compensation, `reopen_incident`, restores it);
- `create_change_request` records `correlation_id = choregos:<key>` and returns the existing change
  when one carries it.

Every text read from ServiceNow is third-party data (a caller wrote it): the tools return it as data and
never act on it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from .client import MAX_ROWS, ServiceNowClient
from .errors import Refused
from .query import Filter, Op, Query, eq, like
from .tables import (
    TABLE_FIELDS,
    TASK_TABLES,
    UPDATABLE_FIELDS,
    check_sys_id,
    check_table,
    is_sys_id,
)

_DATE = "%Y-%m-%d %H:%M:%S"
_MARKER = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "have", "has", "not", "does", "after",
        "before", "when", "since", "about", "into", "today", "please", "there", "their", "they", "them",
        "your", "mine", "cannot", "can't", "doesn't", "work", "works",
    }
)  # fmt: skip
_REFERENCE_FIELDS = {"assignment_group": "sys_user_group", "cmdb_ci": "cmdb_ci"}
_LEVELS = frozenset({"1", "2", "3"})
_TEXT_FIELDS: dict[str, tuple[str, ...]] = {
    "kb_knowledge": ("short_description", "text"),
    "cmdb_ci": ("name", "short_description"),
    "sys_user_group": ("name", "description"),
}
_CLOSED_INCIDENT_LABELS = ("Resolved", "Closed", "Canceled")


def text_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:16]


def marker_of(key: str) -> str:
    if not _MARKER.match(key):
        raise Refused("an action key is 1-80 characters among letters, digits and . _ : -")
    return f"[choregos:{key}]"


def keywords(text: str, limit: int = 5) -> list[str]:
    """The distinctive words of a title, to look for similar records."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", text.lower()) if w not in _STOPWORDS]
    return list(dict.fromkeys(words))[:limit]


def _check_task_table(table: str) -> str:
    check_table(table)
    if table not in TASK_TABLES:
        raise Refused(f"writes are served on {', '.join(sorted(TASK_TABLES))} only, not {table}")
    return table


def _text_group(table: str, terms: list[str]) -> list[Filter]:
    fields = _TEXT_FIELDS.get(table, ("short_description", "description"))
    return [like(f, term) for term in terms for f in fields if f in TABLE_FIELDS[table]]


class ServiceNowTools:
    def __init__(self, client: ServiceNowClient) -> None:
        self.client = client

    # ─────────────────────────── reads ───────────────────────────

    async def search_records(
        self,
        table: str,
        filters: list[Filter] | None = None,
        terms: list[str] | None = None,
        order_by: str | None = None,
        descending: bool = True,
        fields: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        check_table(table)
        query = Query(table).where(*(filters or []))
        for term in terms or []:
            query.any_of(*_text_group(table, [term]))
        default_order = "sys_updated_on" if "sys_updated_on" in TABLE_FIELDS[table] else None
        if order_by or default_order:
            query.order(order_by or str(default_order), descending)
        rows = await self.client.list_records(table, query, fields=fields, limit=limit)
        return {"table": table, "count": len(rows), "records": rows}

    async def get_record(
        self,
        table: str,
        sys_id: str | None = None,
        number: str | None = None,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        check_table(table)
        if sys_id:
            return await self.client.get(table, sys_id, fields=fields)
        if number and "number" in TABLE_FIELDS[table]:
            row = await self.client.find_one(table, Query(table).where(eq("number", number)))
            if row is None:
                raise Refused(f"no {table} numbered {number}")
            return row if not fields else await self.client.get(table, row["sys_id"], fields=fields)
        raise Refused("give a sys_id, or a number on a table that has one")

    async def get_journal(self, table: str, sys_id: str, limit: int = 20) -> dict[str, Any]:
        _check_task_table(table)
        entries = await self.journal_entries(table, sys_id, ("work_notes", "comments"), limit)
        return {"table": table, "sys_id": sys_id, "entries": entries}

    async def journal_entries(
        self, table: str, sys_id: str, elements: tuple[str, ...], limit: int
    ) -> list[dict[str, Any]]:
        check_sys_id(sys_id)
        query = Query("sys_journal_field").where(
            eq("element_id", sys_id),
            eq("name", table),
            Filter(field="element", op=Op.in_, value=list(elements)),
        )
        query.order("sys_created_on", descending=True)
        rows = await self.client.list_records("sys_journal_field", query, limit=limit)
        return [
            {
                "sys_id": r["fields"]["sys_id"],
                "element": r["fields"]["element"],
                "value": r["fields"]["value"],
                "created_on": r["fields"]["sys_created_on"],
                "created_by": r["fields"]["sys_created_by"],
            }
            for r in rows
        ]

    async def get_ci(self, sys_id: str | None = None, name: str | None = None) -> dict[str, Any]:
        if sys_id:
            return await self.client.get("cmdb_ci", sys_id)
        if name:
            row = await self.client.find_one("cmdb_ci", Query("cmdb_ci").where(eq("name", name)))
            if row is None:
                raise Refused(f"no configuration item named {name!r}")
            return row
        raise Refused("give the CI's sys_id or its name")

    async def list_ci_relations(self, sys_id: str, limit: int = MAX_ROWS) -> dict[str, Any]:
        check_sys_id(sys_id)
        query = Query("cmdb_rel_ci").any_of(eq("parent", sys_id), eq("child", sys_id))
        rows = await self.client.list_records("cmdb_rel_ci", query, limit=limit)
        relations = []
        for r in rows:
            f, d = r["fields"], r["display"]
            outbound = f["parent"] == sys_id
            other = "child" if outbound else "parent"
            relations.append(
                {
                    "sys_id": f["sys_id"],
                    "direction": "outbound" if outbound else "inbound",
                    "type": d.get("type", f["type"]),
                    "ci": {"sys_id": f[other], "name": d.get(other, "")},
                }
            )
        return {"sys_id": sys_id, "relations": relations}

    async def search_knowledge(self, terms: list[str], limit: int = 10) -> dict[str, Any]:
        if not terms:
            raise Refused("give at least one term")
        query = Query("kb_knowledge").where(eq("workflow_state", "published"), eq("active", "true"))
        for term in terms:
            query.any_of(*_text_group("kb_knowledge", [term]))
        rows = await self.client.list_records("kb_knowledge", query, limit=limit)
        if not rows and len(terms) > 1:
            # Every term must match above; with several, also accept an article matching any of them.
            query = Query("kb_knowledge").where(eq("workflow_state", "published"), eq("active", "true"))
            query.any_of(*_text_group("kb_knowledge", terms))
            rows = await self.client.list_records("kb_knowledge", query, limit=limit)
        return {"count": len(rows), "articles": rows}

    async def similar_incidents(
        self,
        sys_id: str | None = None,
        cmdb_ci: str | None = None,
        terms: list[str] | None = None,
        days: int = 90,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Resolved or closed incidents of the last `days` days on the same CI or matching the terms."""
        terms = list(terms or [])
        if sys_id:
            source = await self.client.get("incident", sys_id)
            cmdb_ci = cmdb_ci or source["fields"].get("cmdb_ci") or None
            terms = terms or keywords(str(source["fields"].get("short_description") or ""))
        if cmdb_ci:
            check_sys_id(cmdb_ci)
        if not cmdb_ci and not terms:
            raise Refused("give an incident, a CI or terms to compare with")
        days = max(1, min(days, 365))
        since = (datetime.now(UTC) - timedelta(days=days)).strftime(_DATE)
        states = [
            v for v in [await self.client.state_value("incident", label) for label in ("Resolved", "Closed")] if v
        ] or ["6", "7"]
        query = Query("incident").where(
            Filter(field="state", op=Op.in_, value=states),
            Filter(field="resolved_at", op=Op.gte, value=since),
        )
        if sys_id:
            query.where(Filter(field="sys_id", op=Op.ne, value=sys_id))
        alternatives = ([eq("cmdb_ci", cmdb_ci)] if cmdb_ci else []) + _text_group("incident", terms)
        query.any_of(*alternatives)
        query.order("resolved_at", descending=True)
        rows = await self.client.list_records("incident", query, limit=MAX_ROWS)

        def score(row: dict[str, Any]) -> int:
            text = f"{row['fields'].get('short_description', '')} {row['fields'].get('description', '')}".lower()
            same_ci = 2 if cmdb_ci and row["fields"].get("cmdb_ci") == cmdb_ci else 0
            return same_ci + sum(1 for t in terms if t.lower() in text)

        ranked = sorted(rows, key=score, reverse=True)[: max(1, min(limit, MAX_ROWS))]
        return {
            "since": since,
            "cmdb_ci": cmdb_ci,
            "terms": terms,
            "incidents": [{**row, "score": score(row)} for row in ranked],
        }

    # ─────────────────────────── writes ───────────────────────────

    async def add_work_note(self, table: str, sys_id: str, text: str, action_key: str | None = None) -> dict[str, Any]:
        _check_task_table(table)
        check_sys_id(sys_id)
        if not text.strip():
            raise Refused("a work note cannot be empty")
        marker = marker_of(action_key or text_key(table, sys_id, text))
        existing = await self.journal_entries(table, sys_id, ("work_notes",), MAX_ROWS)
        if any(marker in str(entry["value"]) for entry in existing):
            return {"table": table, "sys_id": sys_id, "written": False, "marker": marker}
        await self.client.update(table, sys_id, {"work_notes": f"{text}\n\n{marker}"})
        return {"table": table, "sys_id": sys_id, "written": True, "marker": marker}

    async def add_comment(self, table: str, sys_id: str, text: str) -> dict[str, Any]:
        """A customer-visible comment: always approved by a person (`human_approval` in the descriptor)."""
        _check_task_table(table)
        check_sys_id(sys_id)
        if not text.strip():
            raise Refused("a comment cannot be empty")
        existing = await self.journal_entries(table, sys_id, ("comments",), MAX_ROWS)
        if any(str(entry["value"]).strip() == text.strip() for entry in existing):
            return {"table": table, "sys_id": sys_id, "written": False}
        await self.client.update(table, sys_id, {"comments": text})
        return {"table": table, "sys_id": sys_id, "written": True}

    async def update_record(self, table: str, sys_id: str, fields: dict[str, str]) -> dict[str, Any]:
        _check_task_table(table)
        check_sys_id(sys_id)
        if "priority" in fields:
            raise Refused("priority is never written: ServiceNow computes it from impact and urgency")
        if not fields:
            raise Refused("give at least one field to change")
        refused = sorted(set(fields) - UPDATABLE_FIELDS)
        if refused:
            raise Refused(f"fields not writable: {', '.join(refused)}; allowed: {', '.join(sorted(UPDATABLE_FIELDS))}")
        for name, value in fields.items():
            if not isinstance(value, str):
                raise Refused(f"{name} takes a string")
            if name in ("impact", "urgency") and value not in _LEVELS:
                raise Refused(f"{name} is 1 (high), 2 (medium) or 3 (low)")
            if name in ("assignment_group", "assigned_to", "cmdb_ci") and value and not is_sys_id(value):
                raise Refused(f"{name} takes a sys_id (or an empty string to clear it)")
            if name in _REFERENCE_FIELDS and value:
                await self.client.get(_REFERENCE_FIELDS[name], value, fields=["sys_id"])
        current = await self.client.get(table, sys_id)
        previous = {name: str(current["fields"].get(name) or "") for name in fields}
        updated = await self.client.update(table, sys_id, dict(fields))
        return {
            "table": table,
            "sys_id": sys_id,
            "previous": previous,
            "current": {name: updated["fields"].get(name) for name in fields},
            "record": updated,
        }

    async def resolve_incident(self, sys_id: str, close_code: str, close_notes: str) -> dict[str, Any]:
        check_sys_id(sys_id)
        if not close_code.strip() or not close_notes.strip():
            raise Refused("resolving needs a close code and close notes")
        current = await self.client.get("incident", sys_id)
        previous_state = str(current["fields"].get("state") or "")
        closed = {
            v for v in [await self.client.state_value("incident", label) for label in _CLOSED_INCIDENT_LABELS] if v
        }
        if previous_state in closed:
            return {
                "sys_id": sys_id,
                "resolved": False,
                "previous_state": previous_state,
                "state": previous_state,
            }
        resolved = await self.client.state_value("incident", "Resolved") or "6"
        updated = await self.client.update(
            "incident", sys_id, {"state": resolved, "close_code": close_code, "close_notes": close_notes}
        )
        return {
            "sys_id": sys_id,
            "number": updated["fields"].get("number"),
            "resolved": True,
            "previous_state": previous_state,
            "state": updated["fields"].get("state"),
        }

    async def reopen_incident(self, sys_id: str, state: str | None = None, reason: str | None = None) -> dict[str, Any]:
        """Undo `resolve_incident`: back to `state` (the previous one), or In Progress."""
        check_sys_id(sys_id)
        current = await self.client.get("incident", sys_id)
        now = str(current["fields"].get("state") or "")
        resolved = await self.client.state_value("incident", "Resolved") or "6"
        if now != resolved or state == now:
            # Not resolved, or the compensation of a resolution that changed nothing: nothing to undo.
            return {"sys_id": sys_id, "reopened": False, "state": now}
        choices = await self.client.choices("incident")
        closed = {v for v, label in choices.items() if label in _CLOSED_INCIDENT_LABELS}
        target = state or await self.client.state_value("incident", "In Progress") or "2"
        if target in closed or (choices and target not in choices):
            raise Refused(f"cannot reopen to state {target!r}")
        values: dict[str, Any] = {"state": target}
        if reason:
            values["work_notes"] = reason
        updated = await self.client.update("incident", sys_id, values)
        return {"sys_id": sys_id, "reopened": True, "state": updated["fields"].get("state")}

    async def create_change_request(
        self,
        short_description: str,
        description: str,
        implementation_plan: str,
        backout_plan: str,
        test_plan: str,
        justification: str = "",
        cmdb_ci: str | None = None,
        assignment_group: str | None = None,
        change_type: Literal["normal", "emergency"] = "normal",
        correlation_id: str | None = None,
        action_key: str | None = None,
    ) -> dict[str, Any]:
        if not short_description.strip():
            raise Refused("a change needs a short description")
        for value in (cmdb_ci, assignment_group):
            if value:
                check_sys_id(value)
        key = correlation_id or action_key or text_key(short_description, description, implementation_plan)
        marker_of(key)
        correlation = f"choregos:{key}"
        existing = await self.client.find_one(
            "change_request", Query("change_request").where(eq("correlation_id", correlation))
        )
        if existing is not None:
            return {"created": False, "correlation_id": correlation, "record": existing}
        values: dict[str, Any] = {
            "short_description": short_description,
            "description": description,
            "implementation_plan": implementation_plan,
            "backout_plan": backout_plan,
            "test_plan": test_plan,
            "justification": justification,
            "type": change_type,
            "correlation_id": correlation,
        }
        if cmdb_ci:
            values["cmdb_ci"] = cmdb_ci
        if assignment_group:
            values["assignment_group"] = assignment_group
        record = await self.client.create("change_request", values)
        return {"created": True, "correlation_id": correlation, "record": record}
