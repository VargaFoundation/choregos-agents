# SPDX-License-Identifier: Apache-2.0
"""The closed list of tables this server reads or writes, and the fields it asks for in each.

A table outside this list is refused before any call. Each table has the fields `sysparm_fields`
always names: the server never asks ServiceNow for "every column", so a field added to the instance
(a custom one holding personal data, say) does not leak through by default.
"""

from __future__ import annotations

import re

from .errors import Refused

TABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "incident": (
        "sys_id", "number", "short_description", "description", "state", "impact", "urgency",
        "priority", "category", "subcategory", "assignment_group", "assigned_to", "caller_id",
        "caller_id.vip", "cmdb_ci", "opened_at", "resolved_at", "closed_at", "close_code",
        "close_notes", "correlation_id", "active", "sys_created_on", "sys_updated_on", "sys_mod_count",
    ),
    "sc_req_item": (
        "sys_id", "number", "short_description", "description", "state", "stage", "cat_item", "request",
        "requested_for", "impact", "urgency", "priority", "assignment_group", "assigned_to", "opened_at",
        "closed_at", "active", "sys_created_on", "sys_updated_on", "sys_mod_count",
    ),
    "change_request": (
        "sys_id", "number", "short_description", "description", "state", "type", "risk", "impact",
        "urgency", "priority", "category", "assignment_group", "assigned_to", "cmdb_ci", "start_date",
        "end_date", "justification", "implementation_plan", "backout_plan", "test_plan",
        "correlation_id", "close_code", "close_notes", "active", "sys_created_on", "sys_updated_on",
        "sys_mod_count",
    ),
    "problem": (
        "sys_id", "number", "short_description", "description", "state", "impact", "urgency",
        "priority", "category", "subcategory", "assignment_group", "assigned_to", "cmdb_ci",
        "known_error", "active", "sys_created_on", "sys_updated_on", "sys_mod_count",
    ),
    "kb_knowledge": (
        "sys_id", "number", "short_description", "text", "workflow_state", "kb_category", "valid_to",
        "active", "sys_updated_on",
    ),
    "cmdb_ci": (
        "sys_id", "name", "sys_class_name", "operational_status", "install_status", "ip_address",
        "fqdn", "support_group", "owned_by", "location", "short_description",
    ),
    "cmdb_rel_ci": ("sys_id", "parent", "child", "type"),
    "sys_user_group": ("sys_id", "name", "description", "active", "manager"),
    "sys_choice": ("sys_id", "name", "element", "value", "label", "sequence", "inactive", "language"),
    "sys_journal_field": (
        "sys_id", "element_id", "name", "element", "value", "sys_created_on", "sys_created_by",
    ),
}  # fmt: skip

TABLES: frozenset[str] = frozenset(TABLE_FIELDS)

#: The tables whose records are work items (the tracker profile) and that `update_record` may change.
TASK_TABLES: frozenset[str] = frozenset({"incident", "sc_req_item", "change_request", "problem"})
TRACKER_TABLES: tuple[str, ...] = ("incident", "sc_req_item", "change_request")
NUMBER_PREFIXES: dict[str, str] = {"INC": "incident", "RITM": "sc_req_item", "CHG": "change_request"}

#: What `update_record` may write — never `priority`: ServiceNow computes it from impact and urgency.
UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {"category", "subcategory", "impact", "urgency", "assignment_group", "assigned_to", "cmdb_ci"}
)

_SYS_ID = re.compile(r"^[0-9a-f]{32}$")
_NUMBER = re.compile(r"^(INC|RITM|CHG)[0-9]{7,10}$")


def check_table(table: str) -> str:
    if table not in TABLES:
        raise Refused(f"table {table!r} is not served; allowed: {', '.join(sorted(TABLES))}")
    return table


def check_sys_id(sys_id: str) -> str:
    if not _SYS_ID.match(sys_id):
        raise Refused("a sys_id is 32 lowercase hexadecimal characters")
    return sys_id


def is_sys_id(value: str) -> bool:
    return bool(_SYS_ID.match(value))


def table_of_number(number: str) -> str:
    """The tracker table a record number belongs to (`INC…` → `incident`)."""
    if not _NUMBER.match(number):
        raise Refused(f"{number!r} is not an incident, request item or change number (INC…, RITM…, CHG…)")
    prefix = re.match(r"^[A-Z]+", number)
    assert prefix is not None
    return NUMBER_PREFIXES[prefix.group(0)]


def check_fields(table: str, fields: list[str] | None) -> tuple[str, ...]:
    """The fields to ask for: the table's list, or a subset of it (anything else is refused)."""
    allowed = TABLE_FIELDS[table]
    if not fields:
        return allowed
    unknown = [f for f in fields if f not in allowed]
    if unknown:
        raise Refused(f"fields not served on {table}: {', '.join(unknown)}")
    return tuple(dict.fromkeys(["sys_id", *fields]))
