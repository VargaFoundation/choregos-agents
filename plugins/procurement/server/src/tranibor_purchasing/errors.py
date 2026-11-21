# SPDX-License-Identifier: Apache-2.0
"""How a call fails: refused by a rule of this server, not found, in conflict, or failed in the backend."""

from __future__ import annotations


class PurchasingError(Exception):
    """Base of every failure this server reports. `code` is the word the agent reads first."""

    code = "error"

    def __str__(self) -> str:
        return f"{self.code}: {super().__str__()}"


class Refused(PurchasingError, ValueError):
    """The request breaks a rule of the profile (a bank detail, a malformed value, a missing action_key…).

    Raised before any call to the backend: a refused request never reaches the purchasing system."""

    code = "refused"


class NotFound(PurchasingError):
    """The record the call names does not exist in the purchasing system."""

    code = "not_found"


class Conflict(PurchasingError):
    """The call is well formed but the state of the records forbids it (a duplicate supplier, a reused
    action_key, an archived supplier)."""

    code = "conflict"


class BackendError(PurchasingError):
    """The purchasing system answered with an error or could not be reached."""

    code = "backend_error"


class NotSupported(PurchasingError):
    """The profile asks for something this backend cannot serve honestly (the purchasing system has no such
    record or field). The backend says so and does not approximate; nothing was written."""

    code = "not_supported"
