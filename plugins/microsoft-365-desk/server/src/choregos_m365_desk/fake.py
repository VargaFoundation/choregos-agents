# SPDX-License-Identifier: Apache-2.0
"""A seeded in-memory Microsoft 365, served as an `httpx` transport (`M365_DESK_FAKE=1`).

It answers every host the server calls, as the server calls it:

- **Entra** (`login.microsoftonline.com/<tenant>/oauth2/v2.0/token`): client credentials of the fake
  application, a Graph token or a bot token by scope;
- **Microsoft Graph** (`graph.microsoft.com/v1.0`): the shared mailbox `it-desk@example.test` (Inbox,
  Drafts, Sent Items; categories, single-value extended properties, `createReply`, `send`), any other
  mailbox refused as RBAC for Applications refuses it (403), and one Teams channel's messages;
- **the Bot Framework's OpenID metadata and keys** (`login.botframework.com`): the keys of
  `FakeBotIssuer`, a test issuer with its own RSA key, so tests mint valid, expired or foreign tokens;
- **the Bot Connector** (`smba.trafficmanager.net`): it records each reply and shows it in the thread, as
  Graph would.

The seed, at fixed dates: three Inbox messages — a plain-text one, an HTML one (styles, a script, a
remote image, entities) and one already categorised `Choregos: In progress` — and a Teams thread whose
root message and one reply mention the bot.
"""

from __future__ import annotations

import itertools
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from .config import (
    BOT_ISSUER,
    BOT_SCOPE,
    BOT_TENANT_DEFAULT,
    FAKE_CHANNEL_ID,
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FAKE_MAILBOX,
    FAKE_TEAM_ID,
    FAKE_TENANT_ID,
    GRAPH_SCOPE,
    SERVICE_URL_DEFAULT,
)

DOMAIN = "example.test"
ALEX = f"alex.durand@{DOMAIN}"
SAM = f"sam.petit@{DOMAIN}"
KIM = f"kim.lee@{DOMAIN}"
KEYS_URL = "https://login.botframework.com/v1/.well-known/keys"
ROOT_ID = "1731400000000"
REPLY_ID = "1731400100000"
CONVERSATION_ID = f"{FAKE_CHANNEL_ID};messageid={ROOT_ID}"
_EP_FILTER = re.compile(r"ep/id eq '((?:[^']|'')*)' and ep/value eq '((?:[^']|'')*)'\)\s*$")
_EP_EXPAND = re.compile(r"^singleValueExtendedProperties\(\$filter=id eq '((?:[^']|'')*)'\)$")


def message_id(n: int) -> str:
    """An id shaped like a Graph immutable id (68 characters)."""
    return f"AAkALgAAAAAAHYQDEapmEc2byACqAC-EWg0Adesk{n:024d}AAA="


MAIL_PLAIN = message_id(1)
MAIL_HTML = message_id(2)
MAIL_TAKEN = message_id(3)
HTML_BODY = (
    "<html><head><style>p { color: red }</style><title>Ignored</title></head><body>"
    "<p>Hello desk,</p><p>The printer on floor 3 prints <b>blank pages</b> &amp; jams.</p>"
    '<img src="https://tracker.example.test/pixel.gif" alt="">'
    "<script>alert('never run')</script>"
    "<div>Thanks,<br>Sam</div></body></html>"
)


class FakeBotIssuer:
    """Signs tokens as the Bot Connector does (RS256, `kid`), with a key of its own."""

    def __init__(self, kid: str = "fake-bot-key-1", endorsements: tuple[str, ...] = ("msteams",)) -> None:
        self.kid = kid
        self.endorsements = endorsements
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk(self) -> dict[str, Any]:
        public: dict[str, Any] = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        return {**public, "kid": self.kid, "use": "sig", "alg": "RS256", "endorsements": list(self.endorsements)}

    def mint(
        self,
        *,
        audience: str = FAKE_CLIENT_ID,
        issuer: str = BOT_ISSUER,
        expires_in: int = 3600,
        service_url: str | None = SERVICE_URL_DEFAULT,
        algorithm: str = "RS256",
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {"iss": issuer, "aud": audience, "iat": now - 10, "nbf": now - 10}
        claims["exp"] = now + expires_in
        if service_url is not None:
            claims["serviceurl"] = service_url
        return jwt.encode(claims, self.private_key, algorithm=algorithm, headers={"kid": self.kid})


def activity(
    text: str = "<at>Desk</at> My second screen flickers since the update.",
    *,
    activity_id: str = ROOT_ID,
    root_id: str | None = None,
    kind: str = "message",
    team_id: str = FAKE_TEAM_ID,
    channel_id: str = FAKE_CHANNEL_ID,
    tenant_id: str = FAKE_TENANT_ID,
    service_url: str = SERVICE_URL_DEFAULT,
    bot_id: str = FAKE_CLIENT_ID,
) -> dict[str, Any]:
    """A Teams channel activity as the Bot Connector posts it to the messaging endpoint."""
    return {
        "type": kind,
        "id": activity_id,
        "timestamp": "2026-11-18T08:30:00.000Z",
        "serviceUrl": service_url,
        "channelId": "msteams",
        "from": {"id": "29:camille", "name": "Camille Martin", "aadObjectId": "u-camille"},
        "conversation": {
            "isGroup": True,
            "conversationType": "channel",
            "tenantId": tenant_id,
            "id": f"{channel_id};messageid={root_id or activity_id}",
        },
        "recipient": {"id": f"28:{bot_id}", "name": "Desk"},
        "textFormat": "plain",
        "text": text,
        "entities": [{"type": "mention", "text": "<at>Desk</at>", "mentioned": {"id": f"28:{bot_id}", "name": "Desk"}}],
        "channelData": {
            "teamsChannelId": channel_id,
            "teamsTeamId": team_id,
            "channel": {"id": channel_id},
            "team": {"id": team_id},
            "tenant": {"id": tenant_id},
        },
    }


@dataclass
class _Message:
    id: str
    folder: str
    subject: str
    sender: str
    body: str
    content_type: str
    received: str
    categories: list[str] = field(default_factory=list)
    properties: dict[str, str] = field(default_factory=dict)
    to: list[str] = field(default_factory=list)
    conversation: str = ""
    has_attachments: bool = False

    def render(self, select: str | None) -> dict[str, Any]:
        full: dict[str, Any] = {
            "id": self.id,
            "subject": self.subject,
            "body": {"contentType": self.content_type, "content": self.body},
            "from": {"emailAddress": {"name": self.sender.split("@")[0], "address": self.sender}},
            "sender": {"emailAddress": {"name": self.sender.split("@")[0], "address": self.sender}},
            "toRecipients": [{"emailAddress": {"address": a}} for a in self.to],
            "webLink": f"https://outlook.office365.com/owa/?ItemID={self.id}&exvsurl=1",
            "categories": list(self.categories),
            "receivedDateTime": self.received,
            "lastModifiedDateTime": self.received,
            "conversationId": self.conversation or f"conv-{self.id[-12:]}",
            "hasAttachments": self.has_attachments,
        }
        if not select:
            return full
        wanted = {name.strip() for name in select.split(",")}
        return {k: v for k, v in full.items() if k in wanted or k == "id"}


class FakeMicrosoft:
    def __init__(self, issuer: FakeBotIssuer | None = None) -> None:
        self.issuer = issuer or FakeBotIssuer()
        self.requests: list[httpx.Request] = []
        self.tokens: dict[str, str] = {}
        self.messages: dict[str, _Message] = {}
        self.channel_messages: dict[tuple[str, str], dict[str, Any]] = {}
        self.sent_mail: list[dict[str, Any]] = []
        self.connector_replies: list[dict[str, Any]] = []
        #: The next N Graph calls answer 429 (Retry-After: 0).
        self.throttle = 0
        #: Bot replies are not yet visible in Graph (its replication lag).
        self.graph_lag = False
        #: The Bot Connector refuses an activity id it has seen with 409 (Teams is not known to).
        self.reject_duplicate_ids = False
        #: The inbox's next page points outside Graph.
        self.foreign_next_link = False
        self._ids = itertools.count(100)
        self._seed()

    # ─────────────────────────── seed ───────────────────────────

    def _seed(self) -> None:
        self.add_message(
            MAIL_PLAIN,
            "VPN token expired",
            ALEX,
            "My VPN token expired this morning, I cannot connect.\nCan you reset it?",
            received="2026-11-18T09:00:00Z",
        )
        self.add_message(
            MAIL_HTML, "Printer on floor 3", SAM, HTML_BODY, content_type="html", received="2026-11-18T10:00:00Z"
        )
        self.messages[MAIL_HTML].has_attachments = True
        self.add_message(
            MAIL_TAKEN,
            "New laptop for Kim",
            KIM,
            "Kim starts on Monday and needs a laptop.",
            received="2026-11-18T08:00:00Z",
            categories=["Choregos: In progress", "Blue category"],
        )
        mention = {
            "id": 0,
            "mentionText": "Desk",
            "mentioned": {"application": {"id": FAKE_CLIENT_ID, "displayName": "Desk"}},
        }
        self.channel_messages[(ROOT_ID, ROOT_ID)] = {
            "id": ROOT_ID,
            "replyToId": None,
            "subject": None,
            "createdDateTime": "2026-11-18T08:30:00Z",
            "lastModifiedDateTime": "2026-11-18T08:30:00Z",
            "webUrl": f"https://teams.microsoft.com/l/message/{FAKE_CHANNEL_ID}/{ROOT_ID}",
            "from": {"user": {"id": "u-camille", "displayName": "Camille Martin", "userIdentityType": "aadUser"}},
            "body": {
                "contentType": "html",
                "content": '<p><at id="0">Desk</at> My second screen flickers since the update.</p>',
            },
            "mentions": [mention],
            "attachments": [],
        }
        self.channel_messages[(ROOT_ID, REPLY_ID)] = {
            "id": REPLY_ID,
            "replyToId": ROOT_ID,
            "createdDateTime": "2026-11-18T08:45:00Z",
            "lastModifiedDateTime": "2026-11-18T08:45:00Z",
            "webUrl": f"https://teams.microsoft.com/l/message/{FAKE_CHANNEL_ID}/{REPLY_ID}",
            "from": {"user": {"id": "u-alex", "displayName": "Alex Durand", "userIdentityType": "aadUser"}},
            "body": {"contentType": "html", "content": '<p><at id="0">Desk</at> Same for me on floor 2.</p>'},
            "mentions": [mention],
            "attachments": [],
        }

    def add_message(
        self,
        identifier: str,
        subject: str,
        sender: str,
        body: str,
        *,
        content_type: str = "text",
        received: str = "2026-11-18T07:00:00Z",
        categories: list[str] | None = None,
        folder: str = "inbox",
    ) -> None:
        self.messages[identifier] = _Message(
            identifier, folder, subject, sender, body, content_type, received, list(categories or []), to=[FAKE_MAILBOX]
        )

    def folder(self, name: str) -> list[_Message]:
        return [m for m in self.messages.values() if m.folder == name]

    # ─────────────────────────── routing ───────────────────────────

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "login.microsoftonline.com":
            return self._token(request)
        if host == "login.botframework.com":
            return self._bot_metadata(request)
        if host == "graph.microsoft.com":
            return self._graph(request)
        if host == "smba.trafficmanager.net":
            return self._connector(request)
        return httpx.Response(502, json={"error": {"code": "NoSuchHost"}})

    def _token(self, request: httpx.Request) -> httpx.Response:
        tenant = request.url.path.split("/")[1]
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("client_id") != FAKE_CLIENT_ID or form.get("client_secret") != FAKE_CLIENT_SECRET:
            return httpx.Response(401, json={"error": "invalid_client"})
        scope = form.get("scope")
        if scope == GRAPH_SCOPE and tenant == FAKE_TENANT_ID:
            token = f"graph-{next(self._ids)}"
        elif scope == BOT_SCOPE and tenant in (BOT_TENANT_DEFAULT, FAKE_TENANT_ID):
            token = f"bot-{next(self._ids)}"
        else:
            return httpx.Response(400, json={"error": "invalid_scope"})
        self.tokens[token] = scope
        return httpx.Response(200, json={"access_token": token, "token_type": "Bearer", "expires_in": 3599})

    def _bearer(self, request: httpx.Request, scope: str) -> bool:
        header = request.headers.get("Authorization", "")
        return header.startswith("Bearer ") and self.tokens.get(header[len("Bearer ") :]) == scope

    def _bot_metadata(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/.well-known/openidconfiguration":
            return httpx.Response(
                200,
                json={
                    "issuer": BOT_ISSUER,
                    "authorization_endpoint": "https://invalid.botframework.com",
                    "jwks_uri": KEYS_URL,
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "token_endpoint_auth_methods_supported": ["private_key_jwt"],
                },
            )
        if request.url.path == "/v1/.well-known/keys":
            return httpx.Response(200, json={"keys": [self.issuer.jwk()]})
        return httpx.Response(404)

    # ─────────────────────────── Graph ───────────────────────────

    def _graph(self, request: httpx.Request) -> httpx.Response:
        if not self._bearer(request, GRAPH_SCOPE):
            return _error(401, "InvalidAuthenticationToken")
        if self.throttle > 0:
            self.throttle -= 1
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"code": "TooManyRequests"}})
        raw = request.url.raw_path.decode().split("?", 1)[0]
        if not raw.startswith("/v1.0/"):
            return _error(404, "ResourceNotFound")
        parts = [unquote(p) for p in raw[len("/v1.0/") :].split("/")]
        params = dict(request.url.params.items())
        if parts[0] == "users":
            if len(parts) < 3 or parts[1].lower() != FAKE_MAILBOX:
                # RBAC for Applications: the application reaches the shared mailbox only.
                return _error(403, "ErrorAccessDenied")
            return self._mailbox(request, parts[2:], params)
        if parts[0] == "teams":
            return self._channel(parts[1:], params)
        return _error(404, "ResourceNotFound")

    def _mailbox(self, request: httpx.Request, parts: list[str], params: dict[str, str]) -> httpx.Response:
        method = request.method
        if len(parts) == 3 and parts[0] == "mailFolders" and parts[2] == "messages" and method == "GET":
            return self._list(parts[1].lower(), params)
        if len(parts) >= 2 and parts[0] == "messages":
            found = self.messages.get(parts[1])
            if found is None:
                return _error(404, "ErrorItemNotFound")
            if len(parts) == 2 and method == "GET":
                return httpx.Response(200, json=self._render(found, params))
            if len(parts) == 2 and method == "PATCH":
                return self._patch(found, json.loads(request.content or b"{}"), params)
            if len(parts) == 3 and parts[2] == "createReply" and method == "POST":
                return self._create_reply(found)
            if len(parts) == 3 and parts[2] == "send" and method == "POST":
                return self._send(found)
        return _error(400, "BadRequest")

    def _render(self, message: _Message, params: dict[str, str]) -> dict[str, Any]:
        rendered = message.render(params.get("$select"))
        expand = params.get("$expand")
        if expand:
            matched = _EP_EXPAND.match(expand)
            if matched:
                wanted = matched.group(1).replace("''", "'")
                found = [{"id": k, "value": v} for k, v in message.properties.items() if k == wanted]
                if found:
                    rendered["singleValueExtendedProperties"] = found
        return rendered

    def _list(self, folder: str, params: dict[str, str]) -> httpx.Response:
        rows = sorted(self.folder(folder), key=lambda m: m.received, reverse=True)
        odata_filter = params.get("$filter")
        if odata_filter:
            matched = _EP_FILTER.search(odata_filter)
            if not matched:
                return _error(400, "ErrorInvalidUrlQueryFilter")
            prop, value = matched.group(1).replace("''", "'"), matched.group(2).replace("''", "'")
            rows = [m for m in rows if m.properties.get(prop) == value]
        top = int(params.get("$top", "10"))
        skip = int(params.get("$skip", "0"))
        page = rows[skip : skip + top]
        body: dict[str, Any] = {"value": [m.render(params.get("$select")) for m in page]}
        if skip + top < len(rows):
            host = "https://graph.example.test" if self.foreign_next_link else "https://graph.microsoft.com"
            select = params.get("$select", "")
            body["@odata.nextLink"] = (
                f"{host}/v1.0/users/{FAKE_MAILBOX}/mailFolders/{folder}/messages"
                f"?$select={select}&$top={top}&$skip={skip + top}"
            )
        return httpx.Response(200, json=body)

    def _patch(self, message: _Message, values: dict[str, Any], params: dict[str, str]) -> httpx.Response:
        if "categories" in values:
            message.categories = [str(c) for c in values["categories"]]
        if "body" in values:
            message.body = str(values["body"].get("content", ""))
            message.content_type = str(values["body"].get("contentType", "text")).lower()
        for prop in values.get("singleValueExtendedProperties") or []:
            message.properties[str(prop["id"])] = str(prop["value"])
        return httpx.Response(200, json=message.render(None))

    def _create_reply(self, original: _Message) -> httpx.Response:
        draft = _Message(
            message_id(next(self._ids)),
            "drafts",
            f"RE: {original.subject}",
            FAKE_MAILBOX,
            f"<div>…</div><hr><div>{original.body}</div>",
            "html",
            "2026-11-18T12:00:00Z",
            to=[original.sender],
            conversation=original.conversation or f"conv-{original.id[-12:]}",
        )
        self.messages[draft.id] = draft
        return httpx.Response(201, json=draft.render(None))

    def _send(self, draft: _Message) -> httpx.Response:
        if draft.folder != "drafts":
            return _error(400, "ErrorInvalidOperation")
        draft.folder = "sentitems"
        self.sent_mail.append({"id": draft.id, "to": list(draft.to), "subject": draft.subject, "body": draft.body})
        return httpx.Response(202)

    def _channel(self, parts: list[str], params: dict[str, str]) -> httpx.Response:
        # teams/{team}/channels/{channel}/messages/{root}[/replies[/{reply}]]
        if len(parts) < 5 or parts[1] != "channels" or parts[3] != "messages":
            return _error(400, "BadRequest")
        if parts[0] != FAKE_TEAM_ID or parts[2] != FAKE_CHANNEL_ID:
            return _error(403, "Forbidden")
        root = parts[4]
        if len(parts) == 5:
            found = self.channel_messages.get((root, root))
        elif len(parts) == 6 and parts[5] == "replies":
            replies = [m for (r, i), m in sorted(self.channel_messages.items()) if r == root and i != root]
            visible = [m for m in replies if not (self.graph_lag and m.get("from", {}).get("application"))]
            return httpx.Response(200, json={"value": visible})
        elif len(parts) == 7 and parts[5] == "replies":
            found = self.channel_messages.get((root, parts[6]))
        else:
            return _error(400, "BadRequest")
        return httpx.Response(200, json=found) if found else _error(404, "NotFound")

    # ─────────────────────────── the Bot Connector ───────────────────────────

    def _connector(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or not self._bearer(request, BOT_SCOPE):
            return httpx.Response(401, json={"error": {"code": "Unauthorized"}})
        raw = request.url.raw_path.decode().split("?", 1)[0]
        parts = [unquote(p) for p in raw.strip("/").split("/")]
        # {region}/v3/conversations/{conversation}/activities/{activity}
        if len(parts) != 6 or parts[1:3] != ["v3", "conversations"] or parts[4] != "activities":
            return httpx.Response(404, json={"error": {"code": "NotFound"}})
        conversation, replied_to = parts[3], parts[5]
        sent = json.loads(request.content)
        if self.reject_duplicate_ids and any(r["activity"].get("id") == sent.get("id") for r in self.connector_replies):
            return httpx.Response(409, json={"error": {"code": "Conflict"}})
        new_id = str(1731500000000 + len(self.connector_replies))
        self.connector_replies.append(
            {"region": parts[0], "conversation": conversation, "reply_to": replied_to, "activity": sent, "id": new_id}
        )
        root = conversation.split(";messageid=")[-1]
        self.channel_messages[(root, new_id)] = {
            "id": new_id,
            "replyToId": root,
            "createdDateTime": "2026-11-18T12:00:00Z",
            "from": {"application": {"id": FAKE_CLIENT_ID, "displayName": "Desk"}},
            "body": {"contentType": "html", "content": str(sent.get("text", ""))},
            "mentions": [],
            "attachments": [],
        }
        return httpx.Response(201, json={"id": new_id})


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": "fake"}})
