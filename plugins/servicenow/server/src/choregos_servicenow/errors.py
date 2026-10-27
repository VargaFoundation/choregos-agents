# SPDX-License-Identifier: Apache-2.0
"""The two ways a call fails: refused before reaching ServiceNow, or refused by ServiceNow."""

from __future__ import annotations


class ServiceNowError(Exception):
    """ServiceNow answered with an error, or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Refused(ValueError):
    """The request breaks a rule of this server (closed table list, priority, a malformed filter…).

    Raised before any call to ServiceNow: a refused request never reaches the instance."""
