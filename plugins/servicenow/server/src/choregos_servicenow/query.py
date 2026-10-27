# SPDX-License-Identifier: Apache-2.0
"""Typed filters turned into a ServiceNow encoded query — never a raw query string from the caller.

An encoded query is `field<op>value` conditions joined by `^` (AND) and `^OR` (OR with the condition
just before). A value is free text, so a caller's term must not be able to end its condition and start
another (`foo^ORactive=false`), nor ask the instance to evaluate script (`javascript:` values are run
server-side by ServiceNow). The rules, applied to every value:

- `^` is escaped as `^^`, the encoded-query escape for a literal caret;
- control characters (new lines included) are refused;
- `javascript:` anywhere in a value is refused;
- an `IN` list refuses a comma inside an element (the comma separates elements).

Field names are checked against the table's served fields (`tables.py`); a dot-walk (`caller_id.vip`)
is accepted only from a served reference field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .errors import Refused
from .tables import TABLE_FIELDS


class Op(StrEnum):
    eq = "eq"
    ne = "ne"
    like = "like"
    not_like = "not_like"
    starts_with = "starts_with"
    in_ = "in"
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    is_empty = "is_empty"
    is_not_empty = "is_not_empty"


OPERATORS: dict[Op, str] = {
    Op.eq: "=",
    Op.ne: "!=",
    Op.like: "LIKE",
    Op.not_like: "NOT LIKE",
    Op.starts_with: "STARTSWITH",
    Op.in_: "IN",
    Op.gt: ">",
    Op.gte: ">=",
    Op.lt: "<",
    Op.lte: "<=",
    Op.is_empty: "ISEMPTY",
    Op.is_not_empty: "ISNOTEMPTY",
}
_UNARY = frozenset({Op.is_empty, Op.is_not_empty})


class Filter(BaseModel):
    """One condition: `{"field": "state", "op": "eq", "value": "2"}`. Conditions are joined by AND."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="a field of the table, e.g. state, assignment_group, cmdb_ci")
    op: Op = Op.eq
    value: str | list[str] | None = Field(
        default=None, description="a string; a list for op=in; nothing for is_empty / is_not_empty"
    )


_FIELD = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def escape_value(value: str) -> str:
    """A value as it can stand in a condition: carets doubled, script and control characters refused."""
    if _CONTROL.search(value):
        raise Refused("a filter value cannot contain control characters or new lines")
    if "javascript:" in value.lower():
        raise Refused("a filter value cannot contain 'javascript:'")
    return value.replace("^", "^^")


def check_field(table: str, name: str) -> str:
    if not _FIELD.match(name):
        raise Refused(f"{name!r} is not a field name")
    served = TABLE_FIELDS[table]
    head = name.split(".", 1)[0]
    if name not in served and head not in served:
        raise Refused(f"field {name!r} is not served on {table}")
    return name


def condition(table: str, flt: Filter) -> str:
    name = check_field(table, flt.field)
    operator = OPERATORS[flt.op]
    if flt.op in _UNARY:
        return f"{name}{operator}"
    if flt.value is None:
        raise Refused(f"filter on {name!r} with op {flt.op.value!r} needs a value")
    if flt.op is Op.in_:
        values = [flt.value] if isinstance(flt.value, str) else flt.value
        if not values:
            raise Refused("op=in needs at least one value")
        for v in values:
            if "," in v:
                raise Refused("an op=in value cannot contain a comma")
        return f"{name}{operator}{','.join(escape_value(v) for v in values)}"
    if not isinstance(flt.value, str):
        raise Refused(f"op {flt.op.value!r} takes one string value")
    return f"{name}{operator}{escape_value(flt.value)}"


@dataclass
class Query:
    """An encoded query under construction: AND of groups, each group an OR of conditions."""

    table: str
    groups: list[list[str]] = field(default_factory=list)
    order_by: str | None = None
    descending: bool = False

    def where(self, *filters: Filter) -> Query:
        for flt in filters:
            self.groups.append([condition(self.table, flt)])
        return self

    def any_of(self, *filters: Filter) -> Query:
        """One group whose conditions are joined by OR (nothing added when empty)."""
        conditions = [condition(self.table, flt) for flt in filters]
        if conditions:
            self.groups.append(conditions)
        return self

    def order(self, name: str, descending: bool = False) -> Query:
        self.order_by = check_field(self.table, name)
        self.descending = descending
        return self

    def encode(self) -> str:
        parts = ["^OR".join(group) for group in self.groups]
        if self.order_by:
            parts.append(f"{'ORDERBYDESC' if self.descending else 'ORDERBY'}{self.order_by}")
        return "^".join(parts)


def eq(name: str, value: str) -> Filter:
    return Filter(field=name, op=Op.eq, value=value)


def like(name: str, value: str) -> Filter:
    return Filter(field=name, op=Op.like, value=value)
