# SPDX-License-Identifier: Apache-2.0
"""A call fails with a typed error: its code first, then why. The code is what an agent acts on.

- `identity_unavailable`: no person's token came with the call, or Microsoft Graph refused it (expired,
  revoked). The server has no identity of its own and never calls Graph without the person's token.
- `invalid_arguments`: refused before any call to Graph.
- `forbidden`: Graph refused the person (a permission the tenant has not consented to, a policy).
- `not_found`, `rate_limited`, `upstream_error`: what Graph answered, or that it could not be reached.
"""

from __future__ import annotations

from typing import Literal

Code = Literal[
    "identity_unavailable",
    "invalid_arguments",
    "forbidden",
    "not_found",
    "rate_limited",
    "upstream_error",
]


class Failure(Exception):
    """A typed failure. Its message never carries a token, nor a body Graph returned."""

    def __init__(self, code: Code, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code: Code = code
        self.status = status

    def __str__(self) -> str:
        return f"{self.code}: {self.args[0]}"


def invalid(message: str) -> Failure:
    return Failure("invalid_arguments", message)
