# SPDX-License-Identifier: Apache-2.0
"""A seeded in-memory Microsoft Graph, served as an `httpx` transport (`M365_FAKE=1`).

It answers the four reads the server makes (`/me/calendarView`, `/me/messages`, `/me/chats`,
`/me/chats/{id}/messages`) for a small tenant, `example.test`, seeded relative to the hour it starts:

- `camille.martin@example.test`, whose data it serves for any token;
- `alex.durand@example.test`, with whom Camille has two one-to-one meetings, a meeting of three, a mail
  thread, a one-to-one chat and a group chat;
- `sam.petit@example.test`, a third person: a one-to-one meeting with Camille, a mail about Alex that
  Alex never received, and a group chat without Alex.

Tokens it treats specially: `fake-expired` is refused (401), `fake-calendar-only` reads the calendar
but is refused mail and chats (403, a tenant that consented to `Calendars.Read` only); `fake:<address>`
reads the mailbox of `<address>`, empty for anyone but Camille. Like Graph, its `$search` is fuzzy: it
also matches an address written in a message's text, which the server must not return.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx

from .config import GRAPH_URL

DOMAIN = "example.test"
CAMILLE = f"camille.martin@{DOMAIN}"
ALEX = f"alex.durand@{DOMAIN}"
SAM = f"sam.petit@{DOMAIN}"
ROOM = f"room-river@{DOMAIN}"
NAMES = {CAMILLE: "Camille Martin", ALEX: "Alex Durand", SAM: "Sam Petit", ROOM: "Room River"}
IDS = {CAMILLE: "u-camille", ALEX: "u-alex", SAM: "u-sam"}
EXPIRED = "fake-expired"
CALENDAR_ONLY = "fake-calendar-only"
_SEARCH = re.compile(r'^"participants:([^"]+)"$')


def _who(address: str) -> dict[str, Any]:
    return {"emailAddress": {"name": NAMES.get(address, address), "address": address}}


def _at(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slot(moment: datetime) -> dict[str, str]:
    return {"dateTime": moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"}


def _event(
    subject: str,
    start: datetime,
    minutes: int,
    organizer: str,
    attendees: list[str],
    *,
    owner: str = CAMILLE,
    resources: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "subject": subject,
        "start": _slot(start),
        "end": _slot(start + timedelta(minutes=minutes)),
        "isAllDay": False,
        "organizer": _who(organizer),
        "attendees": [{**_who(a), "type": "required", "status": {"response": "accepted"}} for a in attendees]
        + [{**_who(r), "type": "resource", "status": {"response": "accepted"}} for r in resources],
        "isOrganizer": organizer == owner,
        "isOnlineMeeting": True,
        "isCancelled": False,
        "body": {"contentType": "html", "content": "<p>Never asked for: the body of an event.</p>"},
    }


def _message(
    conversation: str, received: datetime, sender: str, to: list[str], subject: str, preview: str, **extra: Any
) -> dict[str, Any]:
    return {
        "id": f"m-{conversation}-{int(received.timestamp())}",
        "conversationId": conversation,
        "subject": subject,
        "receivedDateTime": _at(received),
        "from": _who(sender),
        "sender": _who(sender),
        "toRecipients": [_who(a) for a in to],
        "ccRecipients": [_who(a) for a in extra.pop("cc", [])],
        "bccRecipients": [],
        "bodyPreview": preview,
        "isDraft": extra.pop("draft", False),
        "hasAttachments": True,
        "body": {"contentType": "html", "content": "<p>Never asked for: the full body.</p>"},
        **extra,
    }


def _chat_message(created: datetime, sender: str, html: str, kind: str = "message") -> dict[str, Any]:
    return {
        "id": f"cm-{int(created.timestamp())}",
        "messageType": kind,
        "createdDateTime": _at(created),
        "deletedDateTime": None,
        "from": {"user": {"id": IDS[sender], "displayName": NAMES[sender]}} if kind == "message" else None,
        "body": {"contentType": "html", "content": html},
        "attachments": [{"id": "a1", "name": "secret-plan.pdf"}],
    }


def _chat(chat_id: str, kind: str, members: list[str], updated: datetime, topic: str | None = None) -> dict[str, Any]:
    return {
        "id": chat_id,
        "chatType": kind,
        "topic": topic,
        "lastUpdatedDateTime": _at(updated),
        "members": [{"userId": IDS[a], "email": a, "displayName": NAMES[a]} for a in members],
    }


@dataclass
class Mailbox:
    events: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    chats: list[dict[str, Any]] = field(default_factory=list)
    chat_messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def seed(now: datetime) -> dict[str, Mailbox]:
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    camille = Mailbox(
        events=[
            _event("Alex / Camille", hour + timedelta(hours=2), 30, CAMILLE, [ALEX], resources=(ROOM,)),
            _event("Roadmap review", hour + timedelta(hours=26), 60, SAM, [CAMILLE, ALEX]),
            _event("Alex / Camille", hour - timedelta(days=7), 30, ALEX, [CAMILLE]),
            _event("Sam / Camille", hour + timedelta(hours=5), 30, CAMILLE, [SAM]),
            _event("Focus time", hour + timedelta(hours=3), 90, CAMILLE, []),
        ],
        messages=[
            _message(
                "conv-timeline",
                hour - timedelta(days=2),
                ALEX,
                [CAMILLE],
                "Project timeline",
                "Here is where the timeline stands. " + "The milestones move by a week. " * 12,
            ),
            _message(
                "conv-timeline",
                hour - timedelta(days=1),
                CAMILLE,
                [ALEX],
                "RE: Project timeline",
                "Thanks, let us talk about it on Thursday.",
                cc=[SAM],
            ),
            _message(
                "conv-budget",
                hour - timedelta(hours=20),
                SAM,
                [CAMILLE],
                "Budget",
                f"Between us: before we tell {ALEX}, the budget is cut.",
            ),
            _message(
                "conv-old",
                hour - timedelta(days=40),
                ALEX,
                [CAMILLE],
                "Old thread",
                "From a while ago.",
            ),
            _message("conv-draft", hour - timedelta(hours=3), CAMILLE, [ALEX], "Draft", "Not sent.", draft=True),
        ],
        chats=[
            _chat("19:alex-camille@unq.gbl.spaces", "oneOnOne", [CAMILLE, ALEX], hour - timedelta(hours=1)),
            _chat("19:launch@thread.v2", "group", [CAMILLE, ALEX, SAM], hour - timedelta(hours=5), "Launch"),
            _chat("19:hiring@thread.v2", "group", [CAMILLE, SAM], hour - timedelta(hours=2), "Hiring"),
            _chat("19:meeting_roadmap@thread.v2", "meeting", [CAMILLE, ALEX, SAM], hour - timedelta(hours=4)),
        ],
        chat_messages={
            "19:alex-camille@unq.gbl.spaces": [
                _chat_message(hour - timedelta(hours=1), CAMILLE, "<p>See you <b>Thursday</b> &amp; thanks!</p>"),
                _chat_message(hour - timedelta(hours=3), ALEX, "<p>Can we move our meeting?</p>"),
                _chat_message(hour - timedelta(days=30), ALEX, "", kind="systemEventMessage"),
            ],
            "19:launch@thread.v2": [
                _chat_message(hour - timedelta(minutes=30), CAMILLE, "", kind="systemEventMessage"),
                _chat_message(hour - timedelta(hours=5), SAM, "<div>Launch moves to Monday.</div>"),
            ],
            "19:hiring@thread.v2": [_chat_message(hour - timedelta(hours=2), SAM, "<p>About Alex's role…</p>")],
            "19:meeting_roadmap@thread.v2": [_chat_message(hour - timedelta(hours=4), ALEX, "<p>Notes</p>")],
        },
    )
    return {CAMILLE: camille}


def _json(status: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})


def _error(status: int, code: str) -> httpx.Response:
    return _json(status, {"error": {"code": code, "message": f"(fake) {code}"}})


class FakeGraph:
    """Callable as an `httpx.MockTransport` handler. `requests` keeps what was asked, for the tests."""

    def __init__(self, now: datetime | None = None) -> None:
        self.mailboxes = seed(now or datetime.now(UTC))
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method != "GET" or not str(request.url).startswith(GRAPH_URL + "/"):
            return _error(400, "BadRequest")
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not token or token == EXPIRED:
            return _error(401, "InvalidAuthenticationToken")
        owner = token.removeprefix("fake:") if token.startswith("fake:") else CAMILLE
        mailbox = self.mailboxes.get(owner.lower(), Mailbox())
        path = unquote(request.url.path).removeprefix("/v1.0")
        query = dict(request.url.params)
        if path != "/me/calendarView" and token == CALENDAR_ONLY:
            return _error(403, "ErrorAccessDenied")
        if path == "/me/calendarView":
            return self._page(request, query, self._calendar(mailbox, query))
        if path == "/me/messages":
            return self._page(request, query, self._search(mailbox, query.get("$search", "")))
        if path == "/me/chats":
            chats = sorted(mailbox.chats, key=lambda c: str(c["lastUpdatedDateTime"]), reverse=True)
            return self._page(request, query, chats)
        if path.startswith("/me/chats/") and path.endswith("/messages"):
            chat_id = path.removeprefix("/me/chats/").removesuffix("/messages")
            if chat_id not in mailbox.chat_messages:
                return _error(404, "NotFound")
            messages = sorted(mailbox.chat_messages[chat_id], key=lambda m: str(m["createdDateTime"]), reverse=True)
            return self._page(request, query, messages)
        return _error(404, "UnknownPath")

    @staticmethod
    def _calendar(mailbox: Mailbox, query: dict[str, str]) -> list[dict[str, Any]]:
        low, high = query.get("startDateTime", ""), query.get("endDateTime", "")
        if not low or not high:
            return []

        def moment(value: str) -> datetime:
            return datetime.fromisoformat(value.replace("Z", "+00:00").replace(".0000000", ""))

        start, end = moment(low), moment(high)
        found = [
            e
            for e in mailbox.events
            if moment(e["start"]["dateTime"]).replace(tzinfo=UTC) < end
            and moment(e["end"]["dateTime"]).replace(tzinfo=UTC) > start
        ]
        return sorted(found, key=lambda e: str(e["start"]["dateTime"]))

    @staticmethod
    def _search(mailbox: Mailbox, search: str) -> list[dict[str, Any]]:
        match = _SEARCH.match(search)
        if match is None:
            return []
        address = match.group(1).lower()
        found = [m for m in mailbox.messages if address in json.dumps(m).lower()]  # fuzzy, as KQL can be
        return sorted(found, key=lambda m: str(m["receivedDateTime"]), reverse=True)

    @staticmethod
    def _page(request: httpx.Request, query: dict[str, str], items: list[dict[str, Any]]) -> httpx.Response:
        top = int(query.get("$top", "10"))
        skip = int(query.get("$skiptoken", "0"))
        body: dict[str, Any] = {"value": items[skip : skip + top]}
        if skip + top < len(items):
            body["@odata.nextLink"] = str(request.url.copy_merge_params({"$skiptoken": str(skip + top)}))
        return _json(200, body)
