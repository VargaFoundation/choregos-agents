# SPDX-License-Identifier: Apache-2.0
"""The two ways a call fails: refused before reaching ServiceNow, or refused by ServiceNow."""

from __future__ import annotations


class ServiceNowError(Exception):
    """ServiceNow answered with an error, or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NotApplied(ServiceNowError):
    """ServiceNow answered 200 to a write and kept the old value of a field it was given.

    A real instance does that silently, for a field the user's role cannot write (a field ACL) or for a
    value the field refuses (a choice that does not exist), and for a transition its state model refuses:
    checked on an Australia instance. Without this check a tool reported the change as made."""

    def __init__(self, table: str, fields: dict[str, tuple[str, str]]) -> None:
        detail = ", ".join(f"{name} (asked {asked!r}, kept {kept!r})" for name, (asked, kept) in sorted(fields.items()))
        super().__init__(
            f"ServiceNow did not apply {detail} on {table}: the integration user's role cannot write it, "
            "or the instance refused the value",
            status=None,
        )
        self.table = table
        self.fields = fields


class Refused(ValueError):
    """The request breaks a rule of this server (closed table list, priority, a malformed filter…).

    Raised before any call to ServiceNow: a refused request never reaches the instance."""
