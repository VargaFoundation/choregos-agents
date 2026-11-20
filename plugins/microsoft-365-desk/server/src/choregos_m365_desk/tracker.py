# SPDX-License-Identifier: Apache-2.0
"""The tracker profile over two intake channels, and the reply to the requester (`TRACKER_PROFILE.md`).

- **The shared mailbox**: each message of its Inbox not yet categorised `Choregos: …` is a candidate;
  the item's state is its `Choregos: <state>` category, the platform's status note is kept in a
  single-value extended property of the message (never seen by the requester), and closing sets
  `Choregos: Done`.
- **A Teams channel**: each message that mentions the bot is an event (`tracker_parse_event`, its JWT
  verified); Teams keeps no state of ours, so the state writes are successful no-ops.

`reply` answers the REQUESTER in the original thread (a mail reply, or a reply in the Teams thread),
idempotent through the platform's `action_key`; it is a governed action's effect, so a workflow decides
its text. `tracker_comment` writes NOTHING to Microsoft 365: the platform comments for its own readers
(a question to a person with a console link, a command's answer), and none of that is the requester's
to read. A key that is not this server's (a child work item's `DESK-1.1`, another tracker's) is a
successful no-op for every write the platform mirrors (state, status, close, comment), and a refusal for
a read and for `reply`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

import structlog

from .botauth import BotAuth
from .config import Channel, Settings, allowed_service_url
from .errors import Failure, Refused
from .keys import MAX_KEY, MailKey, TeamsKey, mail_key, parse_key, teams_key
from .microsoft import Connector, Graph, segment
from .text import BODY_MAX, REPLY_MAX, TITLE_MAX, as_teams_markup, body_text, clip, html_to_text, strip_mentions

CREATED = "tracker.item.created"
#: The category prefix that says Tranibor holds a message: `Choregos: In progress`, `Choregos: Done`.
CATEGORY_PREFIX = "Choregos:"
DONE = "Done"
#: The state of a message no one took yet.
NEW = "New"
#: The property set (a GUID of this plugin's own) of the single-value extended properties it writes.
PROPERTY_SET = "{793633a1-729c-497c-930e-276450befa92}"
ACTION_PROPERTY = f"String {PROPERTY_SET} Name choregos-action"
IMMUTABLE_IDS = {"Prefer": 'IdType="ImmutableId"'}
MESSAGE_FIELDS = (
    "id,subject,body,from,sender,webLink,categories,receivedDateTime,lastModifiedDateTime,conversationId,hasAttachments"
)
PAGE_SIZE = 50
CANDIDATE_PAGES = 10
REPLY_PAGES = 4
MAX_LIST = 200
MAX_EVENT_BODY = 1 << 20
MAX_STATUS = 8000
#: How many conversations' service URLs, and replies sent, one process remembers.
MEMORY = 1024
_MARKER = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_UNSAFE_IN_CATEGORY = re.compile(r"[\x00-\x1f,;]+")

log = structlog.get_logger("choregos_m365_desk.tracker")


def status_property(marker: str) -> str:
    return f"String {PROPERTY_SET} Name choregos-status-{marker}"


def reference(action_key: str) -> str:
    """A short, stable reference of one governed reply: the same action, the same reference."""
    return "desk-" + hashlib.sha256(action_key.encode()).hexdigest()[:12]


def state_of(categories: Any) -> str | None:
    """`Choregos: In progress` → `In progress`; `None` when no category is Tranibor's."""
    for category in categories if isinstance(categories, list) else []:
        if isinstance(category, str) and category.startswith(CATEGORY_PREFIX):
            return category[len(CATEGORY_PREFIX) :].strip() or None
    return None


def _odata(value: str) -> str:
    return value.replace("'", "''")


def _bounded_put(memory: OrderedDict[str, str], key: str, value: str) -> None:
    memory[key] = value
    memory.move_to_end(key)
    while len(memory) > MEMORY:
        memory.popitem(last=False)


class Desk:
    def __init__(self, settings: Settings, graph: Graph, connector: Connector, bot: BotAuth) -> None:
        self.settings = settings
        self.graph = graph
        self.connector = connector
        self.bot = bot
        #: The service URL of the last verified activity of each channel: the one the Bot Connector asks
        #: replies to go to. Lost on a restart, when the configured `service_url` is used instead.
        self._service_urls: OrderedDict[str, str] = OrderedDict()
        #: Teams replies this process sent, by reference: a replay within the process sends nothing.
        self._sent: OrderedDict[str, str] = OrderedDict()

    # ─────────────────────────── helpers ───────────────────────────

    def _mailbox(self) -> str:
        if self.settings.mailbox is None:
            raise Refused("no shared mailbox is configured (M365_DESK_MAILBOX)")
        return f"/users/{segment(self.settings.mailbox)}"

    def _channel(self, key: TeamsKey) -> Channel:
        channel = self.settings.channel(key.channel_id)
        if channel is None:
            raise Refused(f"the channel of {key.key} is not one this desk answers (M365_DESK_TEAMS)")
        return channel

    def _message_path(self, channel: Channel, key: TeamsKey) -> str:
        root = _thread(channel, key)
        return root if key.is_root else f"{root}/replies/{segment(key.activity_id)}"

    def _ours(self, key: str) -> MailKey | TeamsKey | None:
        """The key when it is this desk's AND its channel is configured: what a mirrored write acts on."""
        parsed = parse_key(key)
        if isinstance(parsed, MailKey) and self.settings.mailbox is None:
            return None
        if isinstance(parsed, TeamsKey) and self.settings.channel(parsed.channel_id) is None:
            return None
        return parsed

    async def _mail(self, key: MailKey, select: str, expand: str | None = None) -> dict[str, Any]:
        params = {"$select": select, **({"$expand": expand} if expand else {})}
        try:
            return await self.graph.request(
                "GET", f"{self._mailbox()}/messages/{segment(key.message_id)}", params=params, headers=IMMUTABLE_IDS
            )
        except Failure as failure:
            if failure.code == "not_found":
                raise Refused(f"no message {key.key} in the shared mailbox") from None
            raise

    async def _patch_mail(self, key: MailKey, values: dict[str, Any]) -> None:
        await self.graph.request(
            "PATCH", f"{self._mailbox()}/messages/{segment(key.message_id)}", json=values, headers=IMMUTABLE_IDS
        )

    # ─────────────────────────── reads ───────────────────────────

    async def fetch_item(self, key: str) -> dict[str, Any]:
        parsed = parse_key(key)
        if parsed is None:
            raise Refused(
                f"{key!r} is not a work item of this desk (mail:<message id> or teams:<conversation>;<activity>)"
            )
        if isinstance(parsed, MailKey):
            return await self._fetch_mail(parsed)
        return await self._fetch_teams(parsed)

    async def _fetch_mail(self, key: MailKey) -> dict[str, Any]:
        message = await self._mail(key, MESSAGE_FIELDS)
        state = state_of(message.get("categories"))
        body = _dict(message.get("body"))
        sender = message.get("from") or message.get("sender") or {}
        address = (sender.get("emailAddress") or {}).get("address") if isinstance(sender, dict) else None
        has_attachments = bool(message.get("hasAttachments"))
        return {
            "key": key.key,
            "title": clip(str(message.get("subject") or "(no subject)"), TITLE_MAX),
            "body": clip(body_text(str(body.get("content") or ""), body.get("contentType")), BODY_MAX),
            "url": message.get("webLink"),
            "state": state or NEW,
            "labels": [] if state else [self.settings.start_label],
            "item_type": "mail",
            "assignees": [],
            "author": str(address) if address else None,
            "created_at": message.get("receivedDateTime"),
            "updated_at": message.get("lastModifiedDateTime"),
            "closed_at": None,
            "fields": {
                "channel": "mail",
                "mailbox": self.settings.mailbox,
                "conversation_id": message.get("conversationId"),
                # Attachments are never read: said, so a person knows to look.
                "has_attachments": has_attachments,
            },
            "comments": [],
        }

    async def _fetch_teams(self, key: TeamsKey) -> dict[str, Any]:
        channel = self._channel(key)
        try:
            message = await self.graph.request("GET", self._message_path(channel, key))
        except Failure as failure:
            if failure.code == "not_found":
                raise Refused(f"no message {key.key} in the channel") from None
            raise
        body = _dict(message.get("body"))
        bot_mentions = {
            str(m.get("id"))
            for m in message.get("mentions") or []
            if isinstance(m, dict)
            and ((m.get("mentioned") or {}).get("application") or {}).get("id") == self.settings.bot_id
        }
        content = strip_mentions(str(body.get("content") or ""), bot_mentions)
        text = clip(body_text(content, body.get("contentType")), BODY_MAX)
        subject = str(message.get("subject") or "").strip()
        first_line = next((line for line in text.split("\n") if line.strip()), "")
        sender = message.get("from") or {}
        who = (sender.get("user") or sender.get("application") or {}) if isinstance(sender, dict) else {}
        author = who.get("userPrincipalName") or who.get("displayName") or who.get("id")
        return {
            "key": key.key,
            "title": clip(subject or first_line or "(no text)", TITLE_MAX),
            "body": text,
            "url": message.get("webUrl"),
            # Teams keeps no state of Tranibor's: a Teams item is always new, and labelled to start.
            "state": NEW,
            "labels": [self.settings.start_label],
            "item_type": "teams",
            "assignees": [],
            "author": str(author) if author else None,
            "created_at": message.get("createdDateTime"),
            "updated_at": message.get("lastModifiedDateTime"),
            "closed_at": None,
            "fields": {
                "channel": "teams",
                "team_id": channel.team_id,
                "channel_id": channel.channel_id,
                "conversation_id": key.conversation_id,
                "activity_id": key.activity_id,
                "root_id": key.root_id,
                "has_attachments": bool(message.get("attachments")),
            },
            "comments": [],
        }

    async def list_candidates(self, limit: int = 50) -> dict[str, Any]:
        """Inbox messages not categorised `Choregos: …`, newest first. Teams items come as events only."""
        if self.settings.mailbox is None:
            return {"keys": [], "reason": "no shared mailbox configured: Teams messages arrive as events only"}
        wanted = max(1, min(limit, MAX_LIST))
        keys: list[str] = []
        skipped = 0
        params = {
            "$select": "id,categories,receivedDateTime",
            "$orderby": "receivedDateTime desc",
            "$top": str(PAGE_SIZE),
        }
        pages = self.graph.pages(
            f"{self._mailbox()}/mailFolders/inbox/messages", params, max_pages=CANDIDATE_PAGES, headers=IMMUTABLE_IDS
        )
        async for page in pages:
            for message in page:
                if state_of(message.get("categories")) is not None:
                    continue
                key = mail_key(str(message.get("id") or ""))
                if key is None:
                    skipped += 1
                    continue
                keys.append(key.key)
                if len(keys) >= wanted:
                    break
            if len(keys) >= wanted:
                break
        result: dict[str, Any] = {"keys": keys}
        if skipped:
            log.warning("candidates_skipped", skipped=skipped, reason="message id too long for a key")
            result["reason"] = f"{skipped} message(s) skipped: their id makes a key longer than {MAX_KEY} characters"
            result["skipped"] = skipped
        return result

    async def parse_event(self, headers: Mapping[str, str], body: str) -> dict[str, Any]:
        """A Bot Connector delivery: verified (its JWT), then a mention of the bot in a configured channel
        becomes `tracker.item.created`. A verified activity of another kind gives no event."""
        unverified: dict[str, Any] = {"verified": False, "events": []}
        if not self.settings.teams or len(body.encode()) > MAX_EVENT_BODY:
            return unverified
        try:
            activity = json.loads(body)
        except ValueError:
            return unverified
        if not isinstance(activity, dict):
            return unverified
        authorization = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
        if await self.bot.verify(authorization, activity) is None:
            return unverified
        event = self._event(activity)
        return {"verified": True, "events": [event] if event else []}

    def _event(self, activity: dict[str, Any]) -> dict[str, str] | None:
        def ignored(reason: str) -> dict[str, str] | None:
            log.info("activity_ignored", reason=reason, type=str(activity.get("type"))[:40])
            return None

        if activity.get("type") != "message":
            return ignored("not a message")
        if activity.get("channelId") != "msteams":
            return ignored("not Teams")
        data = _dict(activity.get("channelData"))
        team_id = str((data.get("team") or {}).get("id") or "")
        channel_id = str((data.get("channel") or {}).get("id") or "")
        tenant_id = str((data.get("tenant") or {}).get("id") or "")
        channel = self.settings.channel(channel_id)
        if channel is None or channel.team_id.lower() != team_id.lower():
            return ignored("not a configured channel")
        if tenant_id.lower() != self.settings.tenant_id.lower():
            return ignored("another tenant")
        conversation = _dict(activity.get("conversation"))
        key = teams_key(str(conversation.get("id") or ""), str(activity.get("id") or ""))
        if key is None or key.channel_id != channel_id:
            return ignored("not a channel message of that channel")
        bot = f"28:{self.settings.bot_id}"
        text = str(activity.get("text") or "")
        for entity in activity.get("entities") or []:
            mentioned = _dict(entity).get("mentioned")
            if _dict(entity).get("type") == "mention" and str(_dict(mentioned).get("id") or "") == bot:
                text = text.replace(str(_dict(entity).get("text") or ""), "")
        if not html_to_text(text):
            return ignored("a mention of the bot and nothing else")
        service_url = str(activity.get("serviceUrl") or "")
        if allowed_service_url(service_url):
            _bounded_put(self._service_urls, channel_id, service_url)
        return {"kind": CREATED, "item_key": key.key, "delivery_id": str(activity["id"])}

    # ─────────────────────────── writes ───────────────────────────

    async def create_item(self) -> dict[str, Any]:
        raise Refused("this desk does not create work items: they come from the shared mailbox and the Teams channels")

    async def set_state(
        self, key: str, status: str | None, label: str | None, remove_labels: list[str] | None
    ) -> dict[str, Any]:
        ignored = (["label"] if label else []) + (["remove_labels"] if remove_labels else [])
        parsed = self._ours(key)
        if not isinstance(parsed, MailKey):
            return {"key": key, "changed": False, "state": None, "ignored": ignored}
        wanted = _UNSAFE_IN_CATEGORY.sub(" ", status or "").strip()[:60]
        message = await self._mail(parsed, "id,categories")
        categories = [c for c in message.get("categories") or [] if isinstance(c, str)]
        current = state_of(categories)
        if not wanted or wanted == current:
            return {"key": key, "changed": False, "state": current or NEW, "ignored": ignored}
        kept = [c for c in categories if not c.startswith(CATEGORY_PREFIX)]
        await self._patch_mail(parsed, {"categories": [*kept, f"{CATEGORY_PREFIX} {wanted}"]})
        return {"key": key, "changed": True, "state": wanted, "ignored": ignored}

    async def upsert_status(self, key: str, markdown: str, marker: str) -> dict[str, Any]:
        """The platform's status note, kept in an extended property of the message (one per marker): the
        requester never sees it. Written only when the text changed."""
        parsed = self._ours(key)
        if not isinstance(parsed, MailKey):
            return {"key": key, "written": False}
        if not _MARKER.match(marker):
            raise Refused("a marker is 1-80 letters, digits and . _ : -")
        prop = status_property(marker)
        message = await self._mail(parsed, "id", f"singleValueExtendedProperties($filter=id eq '{_odata(prop)}')")
        current = next(
            (p.get("value") for p in message.get("singleValueExtendedProperties") or [] if isinstance(p, dict)),
            None,
        )
        text = clip(markdown, MAX_STATUS)
        if current == text:
            return {"key": key, "written": False}
        await self._patch_mail(parsed, {"singleValueExtendedProperties": [{"id": prop, "value": text}]})
        return {"key": key, "written": True}

    async def close(self, key: str) -> dict[str, Any]:
        parsed = self._ours(key)
        if not isinstance(parsed, MailKey):
            return {"key": key, "closed": False, "state": None}
        message = await self._mail(parsed, "id,categories")
        categories = [c for c in message.get("categories") or [] if isinstance(c, str)]
        if state_of(categories) == DONE:
            return {"key": key, "closed": False, "state": DONE}
        kept = [c for c in categories if not c.startswith(CATEGORY_PREFIX)]
        await self._patch_mail(parsed, {"categories": [*kept, f"{CATEGORY_PREFIX} {DONE}"]})
        return {"key": key, "closed": True, "state": DONE}

    async def comment(self, key: str, markdown: str, action_key: str | None) -> dict[str, Any]:
        """The profile's comment, which the platform writes for its own readers (a person's question with
        a console link, a command's answer): never sent to the requester, so a successful no-op whatever
        the key. Only `reply`, a governed action's effect, speaks to the requester."""
        action = action_key or _text_action(key, markdown)
        return {"key": key, "id": f"comment:{reference(action)}", "written": False}

    async def reply(self, key: str, text: str, action_key: str | None, work_item_key: str | None) -> dict[str, Any]:
        """Answers the requester in the original thread, once per `action_key`."""
        if not text.strip():
            raise Refused("a reply needs a text")
        if len(text) > REPLY_MAX:
            raise Refused(f"a reply is at most {REPLY_MAX} characters ({len(text)} given)")
        parsed = parse_key(key)
        if parsed is None:
            raise Refused(f"{key!r} is not a work item of this desk: no thread to reply in")
        if work_item_key and parse_key(work_item_key) is not None and work_item_key != key:
            raise Refused("a reply goes to the thread of the action's own work item only")
        action = action_key or _text_action(key, text)
        if isinstance(parsed, MailKey):
            return await self._reply_mail(parsed, text, action)
        return await self._reply_teams(parsed, text, action)

    async def _first(self, path: str, odata_filter: str) -> dict[str, Any] | None:
        found = await self.graph.request(
            "GET", path, params={"$filter": odata_filter, "$select": "id", "$top": "1"}, headers=IMMUTABLE_IDS
        )
        value = found.get("value")
        return value[0] if isinstance(value, list) and value and isinstance(value[0], dict) else None

    async def _reply_mail(self, key: MailKey, text: str, action: str) -> dict[str, Any]:
        """createReply → body and action property → send. A sent message carrying the action is the
        reply already made; a draft carrying it (an attempt cut short) is sent instead of a new one."""
        mailbox = self._mailbox()
        carries_action = (
            f"singleValueExtendedProperties/Any(ep: ep/id eq '{ACTION_PROPERTY}' and ep/value eq '{_odata(action)}')"
        )
        stable_id = f"reply:{reference(action)}"
        sent = await self._first(f"{mailbox}/mailFolders/sentitems/messages", carries_action)
        if sent is not None:
            log.info("reply_already_sent", work_item=key.key, channel="mail")
            return {"key": key.key, "channel": "mail", "id": stable_id, "message_id": sent.get("id"), "sent": False}
        draft = await self._first(f"{mailbox}/mailFolders/drafts/messages", carries_action)
        if draft is None:
            try:
                draft = await self.graph.request(
                    "POST", f"{mailbox}/messages/{segment(key.message_id)}/createReply", json={}, headers=IMMUTABLE_IDS
                )
            except Failure as failure:
                if failure.code == "not_found":
                    raise Refused(f"no message {key.key} in the shared mailbox: no thread to reply in") from None
                raise
            await self.graph.request(
                "PATCH",
                f"{mailbox}/messages/{segment(str(draft['id']))}",
                json={
                    "body": {"contentType": "text", "content": text},
                    "singleValueExtendedProperties": [{"id": ACTION_PROPERTY, "value": action}],
                },
                headers=IMMUTABLE_IDS,
            )
        await self.graph.request("POST", f"{mailbox}/messages/{segment(str(draft['id']))}/send", headers=IMMUTABLE_IDS)
        log.info("reply_sent", work_item=key.key, channel="mail")
        return {"key": key.key, "channel": "mail", "id": stable_id, "message_id": draft.get("id"), "sent": True}

    async def _reply_teams(self, key: TeamsKey, text: str, action: str) -> dict[str, Any]:
        """A reply in the thread through the Bot Connector, carrying a visible reference of the action
        (`Ref: desk-…`): a reply of the bot carrying it in the thread (read with Graph) is the reply
        already made."""
        channel = self._channel(key)
        ref = reference(action)
        stable_id = f"reply:{ref}"
        answer = {"key": key.key, "channel": "teams", "id": stable_id}
        if ref in self._sent or await self._bot_reply_carrying(channel, key, ref):
            log.info("reply_already_sent", work_item=key.key, channel="teams")
            return {**answer, "sent": False}
        service_url = self._service_urls.get(key.channel_id, self.settings.service_url)
        activity = {
            "type": "message",
            # Derived from the action: a connector that keeps activity ids answers a replay with 409.
            "id": ref,
            "replyToId": key.activity_id,
            "textFormat": "xml",
            "text": f"{as_teams_markup(text)}<br><br>Ref: {ref}",
            "from": {"id": f"28:{self.settings.bot_id}"},
            "conversation": {"id": key.conversation_id},
        }
        status, _ = await self.connector.reply(service_url, key.conversation_id, key.activity_id, activity)
        _bounded_put(self._sent, ref, key.key)
        if status == 409:
            log.info("reply_already_sent", work_item=key.key, channel="teams", reason="conflict")
            return {**answer, "sent": False}
        log.info("reply_sent", work_item=key.key, channel="teams")
        return {**answer, "sent": True}

    async def _bot_reply_carrying(self, channel: Channel, key: TeamsKey, ref: str) -> bool:
        root = _thread(channel, key)
        async for page in self.graph.pages(f"{root}/replies", {"$top": str(PAGE_SIZE)}, max_pages=REPLY_PAGES):
            for message in page:
                sender = message.get("from") or {}
                application = (sender.get("application") or {}) if isinstance(sender, dict) else {}
                content = str((message.get("body") or {}).get("content") or "")
                if application.get("id") == self.settings.bot_id and f"Ref: {ref}" in content:
                    return True
        return False


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _thread(channel: Channel, key: TeamsKey) -> str:
    """The Graph path of the thread's root message."""
    team, chan = segment(channel.team_id), segment(channel.channel_id)
    return f"/teams/{team}/channels/{chan}/messages/{segment(key.root_id)}"


def _text_action(key: str, text: str) -> str:
    """Without the platform's key (a direct call), the action is the key and the text, hashed."""
    return "text:" + hashlib.sha256(f"{key}\x1f{text}".encode()).hexdigest()[:32]
