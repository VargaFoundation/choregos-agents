# SPDX-License-Identifier: Apache-2.0
"""A call fails with a typed error: its code first, then why.

- `refused`: the request breaks a rule of this server (an unknown key, a text too long, a reply to
  another work item's thread…). Raised before any call to Microsoft: it never reaches the tenant. The
  tracker profile reads an MCP error whose text starts with `refused:` as a refusal.
- `unauthorized`, `forbidden`, `not_found`, `rate_limited`, `upstream_error`: what Microsoft answered,
  or that it could not be reached.

A message never carries a token, a client secret nor a body Microsoft returned (which may quote mail).
"""

from __future__ import annotations

from typing import Literal

Code = Literal["refused", "unauthorized", "forbidden", "not_found", "rate_limited", "upstream_error"]


class Failure(Exception):
    def __init__(self, code: Code, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code: Code = code
        self.status = status

    def __str__(self) -> str:
        return f"{self.code}: {self.args[0]}"


class Refused(Failure):
    """Refused by this server, before any call to Microsoft."""

    def __init__(self, message: str) -> None:
        super().__init__("refused", message)
