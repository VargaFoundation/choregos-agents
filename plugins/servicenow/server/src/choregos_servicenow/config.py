# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform fills these variables from the plugin's `config_schema` and its sealed `secret_fields`
(`servers/servicenow.yaml`). `SERVICENOW_FAKE=1` replaces the instance with a seeded in-memory one, so
tests and demos need no account.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

#: The credentials the fake instance accepts (they protect nothing: the fake is in memory).
FAKE_BASE_URL = "https://fake.service-now.test"
FAKE_USERNAME = "choregos.integration"
FAKE_PASSWORD = "fake-password"  # noqa: S105 - the fake's own credential, not a secret
FAKE_WEBHOOK_SECRET = "fake-webhook-secret"  # noqa: S105 - idem


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str
    auth: Literal["oauth", "basic"] = "oauth"
    client_id: str | None = None
    client_secret: SecretStr | None = None
    username: str | None = None
    password: SecretStr | None = None
    webhook_secret: SecretStr | None = None
    #: The assignment queue polled by `tracker_list_candidates`: group names or sys_ids.
    assignment_groups: tuple[str, ...] = ()
    fake: bool = False
    port: int = 8080

    @model_validator(mode="after")
    def _credentials_present(self) -> Settings:
        if not self.base_url.startswith("https://") and not self.fake:
            raise ValueError("SERVICENOW_BASE_URL must be an https:// URL")
        if self.auth == "oauth" and not (self.client_id and self.client_secret):
            raise ValueError("SERVICENOW_AUTH=oauth needs SERVICENOW_CLIENT_ID and SERVICENOW_CLIENT_SECRET")
        if self.auth == "basic" and not (self.username and self.password):
            raise ValueError("SERVICENOW_AUTH=basic needs SERVICENOW_USERNAME and SERVICENOW_PASSWORD")
        return self

    @property
    def instance_url(self) -> str:
        return self.base_url.rstrip("/")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        groups = tuple(g.strip() for g in source.get("SERVICENOW_ASSIGNMENT_GROUPS", "").split(",") if g.strip())
        port = int(source.get("PORT", "8080"))
        if source.get("SERVICENOW_FAKE", "") in ("1", "true", "yes"):
            return cls(
                base_url=FAKE_BASE_URL,
                auth="basic",
                username=FAKE_USERNAME,
                password=SecretStr(FAKE_PASSWORD),
                webhook_secret=SecretStr(source.get("SERVICENOW_WEBHOOK_SECRET") or FAKE_WEBHOOK_SECRET),
                assignment_groups=groups or ("Service Desk",),
                fake=True,
                port=port,
            )
        auth = source.get("SERVICENOW_AUTH", "oauth")
        if auth not in ("oauth", "basic"):
            raise ValueError("SERVICENOW_AUTH must be oauth or basic")

        def secret(name: str) -> SecretStr | None:
            value = source.get(name)
            return SecretStr(value) if value else None

        return cls(
            base_url=source.get("SERVICENOW_BASE_URL", ""),
            auth=auth,  # checked just above
            client_id=source.get("SERVICENOW_CLIENT_ID") or None,
            client_secret=secret("SERVICENOW_CLIENT_SECRET"),
            username=source.get("SERVICENOW_USERNAME") or None,
            password=secret("SERVICENOW_PASSWORD"),
            webhook_secret=secret("SERVICENOW_WEBHOOK_SECRET"),
            assignment_groups=groups,
            port=port,
        )
