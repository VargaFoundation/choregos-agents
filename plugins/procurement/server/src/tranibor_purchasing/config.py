# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform fills these variables from the plugin's `config_schema` and its sealed `secret_fields`
(`servers/purchasing.yaml`). `PURCHASING_FAKE=1` replaces the purchasing system with a seeded in-memory
one (`PURCHASING_FAKE_SCENE` picks its data), so tests and demos need no account. The fake is never a
configuration value: only `fake.env` of the descriptor switches it on.

`PURCHASING_BACKEND=odoo` is the one value the configuration accepts: the Odoo backend (`backends/odoo.py`,
PA-06) then needs `PURCHASING_BASE_URL` (https), `PURCHASING_USERNAME` and the secret `PURCHASING_API_KEY`;
`PURCHASING_DATABASE` is optional (sent as the `X-Odoo-Database` header). Any other value, or none, refuses to
start. Reading the settings opens no connection.

`CHOREGOS_SERVER_TOKEN` is the platform's (ADR 0096): the bearer token it presents on every call. The
server refuses to start without it, except on the fake with `CHOREGOS_SERVER_INSECURE_DEV=1` said
explicitly, for a local demo.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

from .backends.fake import scene_name

#: The backends the descriptor's `config_schema` offers, and the ones this version can run.
BACKENDS = ("odoo",)
SERVED_BACKENDS: tuple[str, ...] = ("odoo",)


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
    #: The real system's connection (odoo): the only host the server reaches is the one in `base_url`.
    base_url: str | None = None
    database: str | None = None
    username: str | None = None
    api_key: SecretStr | None = None

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
            raise ValueError(f"PURCHASING_BACKEND={self.backend} is not served by this version")
        if self.backend == "odoo":
            self._odoo_connection()
        return self

    def _odoo_connection(self) -> None:
        """Refuse an incomplete or unsafe connection before anything listens."""
        parts = urlsplit(self.base_url or "")
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("PURCHASING_BASE_URL must be an https URL, such as https://purchasing.example.com")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("PURCHASING_BASE_URL must hold no credential, query or fragment")
        if not self.username:
            raise ValueError("PURCHASING_USERNAME is not set (the dedicated integration user)")
        if self.api_key is None or not self.api_key.get_secret_value():
            raise ValueError("PURCHASING_API_KEY is not set (the integration user's API key)")

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
        raw_key = source.get("PURCHASING_API_KEY")
        return cls(
            backend=source.get("PURCHASING_BACKEND") or None,
            base_url=(source.get("PURCHASING_BASE_URL") or "").rstrip("/") or None,
            database=source.get("PURCHASING_DATABASE") or None,
            username=source.get("PURCHASING_USERNAME") or None,
            api_key=SecretStr(raw_key) if raw_key else None,
            port=port,
            server_token=server_token,
            insecure_dev=insecure_dev,
        )
