# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform fills these variables from the plugin's `config_schema` and its sealed `secret_fields`
(`servers/purchasing.yaml`). `PURCHASING_FAKE=1` replaces the purchasing system with a seeded in-memory
one (`PURCHASING_FAKE_SCENE` picks its data), so tests and demos need no account. The fake is never a
configuration value: only `fake.env` of the descriptor switches it on.

**This version serves no real backend.** `PURCHASING_BACKEND=odoo` is the one value the configuration
accepts, and the server refuses to start on it, saying the Odoo backend arrives with a later version
(PA-06). Until then only the fake serves the profile.

`CHOREGOS_SERVER_TOKEN` is the platform's (ADR 0096): the bearer token it presents on every call. The
server refuses to start without it, except on the fake with `CHOREGOS_SERVER_INSECURE_DEV=1` said
explicitly, for a local demo.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

from .backends.fake import scene_name

#: The backends the descriptor's `config_schema` offers, and the ones this version can run.
BACKENDS = ("odoo",)
SERVED_BACKENDS: tuple[str, ...] = ()


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: A name of `BACKENDS`; `None` on the fake.
    backend: str | None = None
    fake: bool = False
    scene: str = "default"
    port: int = 8080
    #: The bearer token the platform presents on every call but the health probe (ADR 0096).
    server_token: SecretStr | None = None
    #: `CHOREGOS_SERVER_INSECURE_DEV=1`: no token, accepted on the fake only.
    insecure_dev: bool = False

    @model_validator(mode="after")
    def _a_backend_this_version_serves(self) -> Settings:
        if self.fake:
            return self
        if self.backend is None:
            raise ValueError("PURCHASING_BACKEND is not set (the backends are: " + ", ".join(BACKENDS) + ")")
        if self.backend not in BACKENDS:
            raise ValueError(
                f"PURCHASING_BACKEND={self.backend!r} is unknown (the backends are: {', '.join(BACKENDS)})"
            )
        if self.backend not in SERVED_BACKENDS:
            raise ValueError(
                f"PURCHASING_BACKEND={self.backend} is not served by this version: the {self.backend} backend arrives "
                "with a later version of the plugin (PA-06); only the seeded fake runs today"
            )
        return self

    def require_server_token(self) -> SecretStr | None:
        """The token every call must carry; `None` only on the fake in explicit insecure dev mode."""
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "CHOREGOS_SERVER_TOKEN is not set: the server refuses to start without the platform's token "
            "(only PURCHASING_FAKE=1 with CHOREGOS_SERVER_INSECURE_DEV=1 runs without it)"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        raw_token = source.get("CHOREGOS_SERVER_TOKEN")
        server_token = SecretStr(raw_token) if raw_token else None
        port = int(source.get("PORT", "8080"))
        insecure_dev = source.get("CHOREGOS_SERVER_INSECURE_DEV", "") == "1"
        if source.get("PURCHASING_FAKE", "") in ("1", "true", "yes"):
            scene = scene_name(source.get("PURCHASING_FAKE_SCENE") or "default")
            return cls(fake=True, scene=scene, port=port, server_token=server_token, insecure_dev=insecure_dev)
        return cls(
            backend=source.get("PURCHASING_BACKEND") or None,
            port=port,
            server_token=server_token,
            insecure_dev=insecure_dev,
        )
