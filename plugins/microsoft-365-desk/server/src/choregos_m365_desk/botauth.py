# SPDX-License-Identifier: Apache-2.0
"""Verifying that a delivery comes from the Bot Connector: its JWT, as the Bot Framework specifies it.

The Bot Connector signs every activity it posts to the bot's messaging endpoint with a JWT in
`Authorization: Bearer …`. The platform forwards that header to this server only because its declaration
says `webhook: {forward_authorization: true}` (the core's ADR 0198); it neither logs nor stores it, and
neither does this server. A token is accepted when:

- it is RS256, signed by a key of the Bot Framework's OpenID metadata
  (`https://login.botframework.com/v1/.well-known/openidconfiguration` → `jwks_uri`, on
  `login.botframework.com` only), kept 24 hours and re-read at most every 5 minutes for an unknown `kid`;
- that key is endorsed for the activity's channel (`endorsements` holds `msteams`) when it lists any;
- its issuer is `https://api.botframework.com`, its audience the bot's Microsoft App ID, and it has not
  expired (5 minutes of clock skew allowed);
- its `serviceurl` claim, when present, is the activity's `serviceUrl`.

Anything else — no header, another scheme, a malformed token, an unknown key, keys that cannot be read —
is "not verified": the delivery is answered `{verified: false}` and nothing of it is used.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
import structlog

from .config import BOT_ISSUER, BOT_KEYS_HOST, BOT_OPENID_URL

KEYS_TTL_S = 24 * 3600
#: An unknown `kid` re-reads the keys (a rotation), at most this often: a forged `kid` cannot make the
#: server hammer the metadata endpoint.
KEYS_REFRESH_MIN_S = 300
LEEWAY_S = 300

log = structlog.get_logger("choregos_m365_desk.botauth")


class BotAuth:
    def __init__(self, http: httpx.AsyncClient, bot_app_id: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.http = http
        self.bot_app_id = bot_app_id
        self.clock = clock
        self._keys: dict[str, dict[str, Any]] = {}
        self._read_at: float | None = None

    async def verify(self, authorization: str | None, activity: Mapping[str, Any]) -> dict[str, Any] | None:
        """The token's claims when the delivery is the Bot Connector's for this bot, else `None`."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return _refused("no bearer token")
        token = token.strip()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return _refused("malformed token")
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str):
            return _refused("not an RS256 token with a key id")
        jwk = await self._key(kid)
        if jwk is None:
            return _refused("unknown signing key")
        endorsements = jwk.get("endorsements")
        channel = str(activity.get("channelId") or "")
        if isinstance(endorsements, list) and endorsements and channel not in endorsements:
            return _refused("the signing key is not endorsed for the activity's channel")
        try:
            key = jwt.PyJWK(jwk, algorithm="RS256").key
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.bot_app_id,
                issuer=BOT_ISSUER,
                leeway=LEEWAY_S,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as error:
            return _refused(f"token rejected ({type(error).__name__})")
        service_claim = claims.get("serviceurl", claims.get("serviceUrl"))
        if service_claim is not None and not _same_url(str(service_claim), str(activity.get("serviceUrl") or "")):
            return _refused("the activity's serviceUrl is not the token's")
        return claims

    async def _key(self, kid: str) -> dict[str, Any] | None:
        now = self.clock()
        stale = self._read_at is None or now - self._read_at > KEYS_TTL_S
        unknown_and_may_refresh = kid not in self._keys and (
            self._read_at is None or now - self._read_at > KEYS_REFRESH_MIN_S
        )
        if stale or unknown_and_may_refresh:
            try:
                self._keys = await self._read_keys()
                self._read_at = now
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                log.warning("bot_keys_unavailable", reason=type(error).__name__)
        return self._keys.get(kid)

    async def _read_keys(self) -> dict[str, dict[str, Any]]:
        metadata = await self.http.get(BOT_OPENID_URL)
        metadata.raise_for_status()
        jwks_uri = str(metadata.json()["jwks_uri"])
        where = urlsplit(jwks_uri)
        if where.scheme != "https" or where.hostname != BOT_KEYS_HOST:
            raise ValueError("the Bot Framework's metadata names keys outside login.botframework.com")
        found = await self.http.get(jwks_uri)
        found.raise_for_status()
        keys = found.json()["keys"]
        return {str(k["kid"]): dict(k) for k in keys if isinstance(k, dict) and isinstance(k.get("kid"), str)}


def _same_url(a: str, b: str) -> bool:
    return a.rstrip("/").lower() == b.rstrip("/").lower()


def _refused(reason: str) -> dict[str, Any] | None:
    """`None` (not verified), said in the log with why; never with the token."""
    log.info("bot_token_refused", reason=reason)
    return None
