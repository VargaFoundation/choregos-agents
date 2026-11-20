# SPDX-License-Identifier: Apache-2.0
"""Configuration, read from the environment once at start-up.

The platform fills these variables from the plugin's `config_schema` and its sealed `secret_fields`
(`servers/desk.yaml`). At least one intake channel is configured: the shared mailbox
(`M365_DESK_MAILBOX`), the Teams channels (`M365_DESK_TEAMS`), or both.

The hosts the server talks to are fixed, not configurable: an administrator cannot point the
application's credentials at another host. The one exception is the Bot Connector's service URL, which
must stay on `smba.trafficmanager.net` (the egress the declaration allows).

`M365_DESK_FAKE=1` replaces Microsoft 365 with a seeded in-memory tenant (`fake.py`), so tests and demos
need no account. `CHOREGOS_SERVER_TOKEN` is the platform's (ADR 0094): the bearer token it presents on
every call. The server refuses to start without it, except on the fake tenant with
`CHOREGOS_SERVER_INSECURE_DEV=1` said explicitly, for a local demo.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

GRAPH_URL = "https://graph.microsoft.com/v1.0"
LOGIN_URL = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
#: The Bot Framework: the tokens it signs (inbound), and the scope of the bot's own token (outbound).
BOT_OPENID_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
BOT_KEYS_HOST = "login.botframework.com"
BOT_ISSUER = "https://api.botframework.com"
BOT_SCOPE = "https://api.botframework.com/.default"
#: Multi-tenant bots get their token from this tenant; single-tenant bots from their own.
BOT_TENANT_DEFAULT = "botframework.com"
#: The Bot Connector hosts a reply may be sent to (the egress of `servers/desk.yaml`).
SERVICE_HOSTS = frozenset({"smba.trafficmanager.net"})
SERVICE_URL_DEFAULT = "https://smba.trafficmanager.net/teams/"
START_LABEL_DEFAULT = "agent-ready"

GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
#: A Teams channel id: `19:<id>@thread.tacv2` (or `@thread.skype` for older channels).
CHANNEL_ID = re.compile(r"^19:[A-Za-z0-9_-]{1,64}@thread\.(?:tacv2|skype)$")
MAILBOX = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,63}$")
LABEL = re.compile(r"^[A-Za-z0-9 ._:/-]{1,64}$")

#: The fake tenant (`M365_DESK_FAKE=1`): nothing here is a secret, everything lives in memory.
FAKE_TENANT_ID = "00000000-0000-4000-8000-00000000d35c"
FAKE_CLIENT_ID = "11111111-1111-4111-8111-111111111111"
FAKE_CLIENT_SECRET = "fake-client-secret"  # noqa: S105 - the fake tenant's own credential, not a secret
FAKE_MAILBOX = "it-desk@example.test"
FAKE_TEAM_ID = "22222222-2222-4222-8222-222222222222"
FAKE_CHANNEL_ID = "19:4f1c2a9b7d3e4c5f8a6b0c1d2e3f4a5b@thread.tacv2"


class Channel(BaseModel):
    """A Teams channel whose bot mentions become work items."""

    model_config = ConfigDict(frozen=True)

    team_id: str
    channel_id: str

    @model_validator(mode="after")
    def _shape(self) -> Channel:
        if not GUID.match(self.team_id):
            raise ValueError(f"team_id {self.team_id!r} is not a team's group id (a GUID)")
        if not CHANNEL_ID.match(self.channel_id):
            raise ValueError(f"channel_id {self.channel_id!r} is not a channel id (19:…@thread.tacv2)")
        return self


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    client_id: str
    client_secret: SecretStr
    #: The shared mailbox read and answered (Graph application permissions, scoped to it by RBAC).
    mailbox: str | None = None
    #: The Teams channels the bot answers in.
    teams: tuple[Channel, ...] = ()
    #: The bot's Microsoft App ID: the audience of the tokens the Bot Connector signs. The same Entra
    #: application as `client_id` unless said otherwise; its secret is `client_secret`.
    bot_app_id: str | None = None
    bot_tenant_id: str = BOT_TENANT_DEFAULT
    #: Where replies go when no verified activity of the conversation told the server its own.
    service_url: str = SERVICE_URL_DEFAULT
    #: The label a work item carries while nobody took it (the project's start label).
    start_label: str = START_LABEL_DEFAULT
    fake: bool = False
    port: int = 8080
    #: The bearer token the platform presents on every call but the health probe (ADR 0094).
    server_token: SecretStr | None = None
    #: `CHOREGOS_SERVER_INSECURE_DEV=1`: no token, accepted on the fake tenant only.
    insecure_dev: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> Settings:
        if not GUID.match(self.tenant_id):
            raise ValueError("M365_DESK_TENANT_ID must be the tenant's id (a GUID)")
        if not GUID.match(self.client_id):
            raise ValueError("M365_DESK_CLIENT_ID must be the application (client) id (a GUID)")
        if not self.client_secret.get_secret_value():
            raise ValueError("M365_DESK_CLIENT_SECRET is required")
        if self.mailbox is None and not self.teams:
            raise ValueError("configure at least one channel: M365_DESK_MAILBOX, M365_DESK_TEAMS, or both")
        if self.mailbox is not None and not MAILBOX.match(self.mailbox):
            raise ValueError("M365_DESK_MAILBOX must be the shared mailbox's address")
        if self.bot_app_id is not None and not GUID.match(self.bot_app_id):
            raise ValueError("M365_DESK_BOT_APP_ID must be the bot's Microsoft App ID (a GUID)")
        if self.bot_tenant_id != BOT_TENANT_DEFAULT and not GUID.match(self.bot_tenant_id):
            raise ValueError("M365_DESK_BOT_TENANT_ID is botframework.com or the single-tenant bot's tenant id")
        if not allowed_service_url(self.service_url):
            raise ValueError(f"M365_DESK_SERVICE_URL must be an https URL on {', '.join(sorted(SERVICE_HOSTS))}")
        if not LABEL.match(self.start_label):
            raise ValueError("M365_DESK_START_LABEL: 1-64 letters, digits, spaces and . _ : / -")
        return self

    @property
    def bot_id(self) -> str:
        """The bot's Microsoft App ID (the application's, unless configured apart)."""
        return self.bot_app_id or self.client_id

    def channel(self, channel_id: str) -> Channel | None:
        return next((c for c in self.teams if c.channel_id == channel_id), None)

    def require_server_token(self) -> SecretStr | None:
        """The token every call must carry; `None` only on the fake tenant in explicit insecure dev mode."""
        if self.server_token is not None and self.server_token.get_secret_value():
            return self.server_token
        if self.fake and self.insecure_dev:
            return None
        raise ValueError(
            "CHOREGOS_SERVER_TOKEN is not set: the server refuses to start without the platform's token "
            "(only M365_DESK_FAKE=1 with CHOREGOS_SERVER_INSECURE_DEV=1 runs without it)"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        port = int(source.get("PORT", "8080"))
        token = source.get("CHOREGOS_SERVER_TOKEN")
        server_token = SecretStr(token) if token else None
        insecure_dev = source.get("CHOREGOS_SERVER_INSECURE_DEV", "") == "1"
        if source.get("M365_DESK_FAKE", "") in ("1", "true", "yes"):
            # The fake tenant only: nothing of a real configuration is read, so none can leak into it.
            return cls(
                tenant_id=FAKE_TENANT_ID,
                client_id=FAKE_CLIENT_ID,
                client_secret=SecretStr(FAKE_CLIENT_SECRET),
                mailbox=FAKE_MAILBOX,
                teams=(Channel(team_id=FAKE_TEAM_ID, channel_id=FAKE_CHANNEL_ID),),
                fake=True,
                port=port,
                server_token=server_token,
                insecure_dev=insecure_dev,
            )
        return cls(
            tenant_id=source.get("M365_DESK_TENANT_ID", ""),
            client_id=source.get("M365_DESK_CLIENT_ID", ""),
            client_secret=SecretStr(source.get("M365_DESK_CLIENT_SECRET", "")),
            mailbox=source.get("M365_DESK_MAILBOX") or None,
            teams=_channels(source.get("M365_DESK_TEAMS", "")),
            bot_app_id=source.get("M365_DESK_BOT_APP_ID") or None,
            bot_tenant_id=source.get("M365_DESK_BOT_TENANT_ID") or BOT_TENANT_DEFAULT,
            service_url=source.get("M365_DESK_SERVICE_URL") or SERVICE_URL_DEFAULT,
            start_label=source.get("M365_DESK_START_LABEL") or START_LABEL_DEFAULT,
            port=port,
            server_token=server_token,
            insecure_dev=insecure_dev,
        )


def _channels(raw: str) -> tuple[Channel, ...]:
    """`M365_DESK_TEAMS`: a JSON list of `{team_id, channel_id}` (the platform renders the field so)."""
    if not raw.strip():
        return ()
    try:
        loaded = json.loads(raw)
    except ValueError as error:
        raise ValueError("M365_DESK_TEAMS must be a JSON list of {team_id, channel_id}") from error
    if not isinstance(loaded, list) or not all(isinstance(item, dict) for item in loaded):
        raise ValueError("M365_DESK_TEAMS must be a JSON list of {team_id, channel_id}")
    return tuple(Channel(team_id=str(i.get("team_id", "")), channel_id=str(i.get("channel_id", ""))) for i in loaded)


def allowed_service_url(url: str) -> bool:
    """A Bot Connector URL the bot's token may be sent to: https, on an allowed host, no credentials."""
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and (parts.hostname or "") in SERVICE_HOSTS
        and parts.port is None
        and not parts.username
        and not parts.query
        and not parts.fragment
    )
