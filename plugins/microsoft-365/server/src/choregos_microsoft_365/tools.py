# SPDX-License-Identifier: Apache-2.0
"""The three reads, over Graph with the person's token, minimised on the server.

Minimisation, whatever Graph returns:

- at most `MAX_ITEMS` (20) items per call, and at most `MAX_ATTENDEES` people listed per event;
- never a full body: a snippet of at most `SNIPPET_CHARS` (300) characters, as plain text;
- never an attachment, nor whether there is one: attachments are never asked of Graph;
- a message or a chat is returned only when the other person takes part in it (sender or recipient of
  the message, member of the chat), checked here on what Graph returned, not left to Graph's search;
- a chat or a mail thread shows the latest exchange only, never its other members' names.

Every result carries `source` and `as_of` (when it was read).
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel

from .errors import invalid
from .graph import Graph

SOURCE = "microsoft-graph"
MAX_ITEMS = 20
MAX_ATTENDEES = 20
SNIPPET_CHARS = 300
#: The widest calendar window one call reads, and how far back mail and chats are searched.
MAX_WINDOW = timedelta(days=92)
MAX_LOOKBACK = timedelta(days=366)
PAGE = 50
MAX_PAGES = 4
EVENT_PAGES = 10
UTC_PREFERENCE = {"Prefer": 'outlook.timezone="UTC"'}

#: An address as Graph reports it. No quote, no space: the address goes into a `$search` string.
_ADDRESS = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})+$")
_TAG = re.compile(r"<[^>]*>")
_BLANK = re.compile(r"\s+")
_FRACTION = re.compile(r"(\.\d{6})\d+")


class Person(BaseModel):
    name: str | None = None
    address: str | None = None


class Attendee(Person):
    response: str | None = None
    type: str | None = None


class Event(BaseModel):
    subject: str | None
    start: str | None
    end: str | None
    is_all_day: bool
    organizer: Person | None
    attendees: list[Attendee]
    attendee_count: int
    #: Exactly two people take part (organizer and attendees, rooms and equipment aside).
    one_to_one: bool
    is_organizer: bool
    is_online_meeting: bool
    is_cancelled: bool


class Events(BaseModel):
    source: str = SOURCE
    as_of: str
    start: str
    end: str
    with_upn: str | None
    events: list[Event]
    #: More events matched than were returned.
    truncated: bool


class MailThread(BaseModel):
    subject: str | None
    #: When the latest message of the thread the two share was received.
    date: str | None
    sender: Person | None
    snippet: str
    #: How many messages of the thread the two share were found since `since`.
    message_count: int


class Mail(BaseModel):
    source: str = SOURCE
    as_of: str
    with_upn: str
    since: str
    threads: list[MailThread]
    truncated: bool


class Chat(BaseModel):
    chat_type: Literal["one_on_one", "group"]
    topic: str | None
    member_count: int
    #: The latest message since `since`.
    date: str | None
    sender: Person | None
    snippet: str


class Chats(BaseModel):
    source: str = SOURCE
    as_of: str
    with_upn: str
    since: str
    chats: list[Chat]
    truncated: bool


# ─────────────────────────── helpers ───────────────────────────


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_moment(value: str, name: str) -> datetime:
    """An ISO 8601 date or date-time; without an offset, UTC."""
    text = value.strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise invalid(f"`{name}` is not an ISO 8601 date or date-time: {text[:40]!r}") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def graph_moment(value: Any) -> datetime | None:
    """A Graph date-time: `2026-11-14T10:00:00Z`, or `{dateTime, timeZone}` asked in UTC."""
    if isinstance(value, dict):
        value = value.get("dateTime")
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(_FRACTION.sub(r"\1", value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def address_of(value: str, name: str = "with_upn") -> str:
    text = value.strip()
    if not _ADDRESS.match(text):
        raise invalid(f"`{name}` is a user principal name or an e-mail address (name@domain)")
    return text.lower()


def snippet(text: Any, *, markup: bool = False) -> str:
    """Plain text, at most `SNIPPET_CHARS` characters."""
    if not isinstance(text, str):
        return ""
    plain: str = _TAG.sub(" ", text) if markup else text
    plain = _BLANK.sub(" ", html.unescape(plain)).strip()
    return plain if len(plain) <= SNIPPET_CHARS else plain[: SNIPPET_CHARS - 1].rstrip() + "…"


def _person(recipient: Any) -> Person | None:
    email = recipient.get("emailAddress") if isinstance(recipient, dict) else None
    if not isinstance(email, dict):
        return None
    address = email.get("address")
    name = email.get("name")
    return Person(
        name=name if isinstance(name, str) else None,
        address=address.lower() if isinstance(address, str) and address else None,
    )


def _addresses(*people: Person | None) -> set[str]:
    return {p.address for p in people if p is not None and p.address}


def _limit(value: int) -> int:
    if not 1 <= value <= MAX_ITEMS:
        raise invalid(f"`limit` is between 1 and {MAX_ITEMS}")
    return value


class Tools:
    def __init__(self, graph: Graph, clock: Callable[[], datetime] | None = None) -> None:
        self.graph = graph
        self.clock = clock or (lambda: datetime.now(UTC))

    def _since(self, value: str) -> datetime:
        now = self.clock()
        since = parse_moment(value, "since")
        if since > now:
            raise invalid("`since` is in the future")
        if now - since > MAX_LOOKBACK:
            raise invalid(f"`since` goes back at most {MAX_LOOKBACK.days} days")
        return since

    # ─────────────────────────── calendar ───────────────────────────

    async def list_events_with(
        self,
        token: str,
        start: str | None = None,
        end: str | None = None,
        within_hours: int | None = None,
        with_upn: str | None = None,
        one_to_one_only: bool = False,
        newest_first: bool = False,
        limit: int = MAX_ITEMS,
    ) -> Events:
        now = self.clock()
        limit = _limit(limit)
        if within_hours is not None:
            if start is not None or end is not None:
                raise invalid("give either `start` and `end`, or `within_hours`, not both")
            if not 1 <= within_hours <= MAX_WINDOW.days * 24:
                raise invalid(f"`within_hours` is between 1 and {MAX_WINDOW.days * 24}")
            low, high = now, now + timedelta(hours=within_hours)
        elif start is not None and end is not None:
            low, high = parse_moment(start, "start"), parse_moment(end, "end")
        else:
            raise invalid("give `start` and `end` (ISO 8601), or `within_hours`")
        if high <= low:
            raise invalid("`end` is after `start`")
        if high - low > MAX_WINDOW:
            raise invalid(f"the window spans at most {MAX_WINDOW.days} days")
        other = address_of(with_upn) if with_upn else None

        params = {
            "startDateTime": iso(low),
            "endDateTime": iso(high),
            "$top": str(PAGE),
            "$select": "subject,start,end,isAllDay,organizer,attendees,isOrganizer,isOnlineMeeting,isCancelled",
            "$orderby": "start/dateTime",
        }
        matched: list[Event] = []
        async for page in self.graph.pages(
            token, "/me/calendarView", params, max_pages=EVENT_PAGES, headers=UTC_PREFERENCE
        ):
            for raw in page:
                event = _event(raw)
                people = _addresses(event.organizer, *(a for a in event.all_attendees if a.type != "resource"))
                if other is not None and other not in people:
                    continue
                if one_to_one_only and len(people) != 2:
                    continue
                matched.append(event.public(len(people) == 2))
        matched.sort(key=lambda e: e.start or "", reverse=newest_first)
        return Events(
            as_of=iso(now),
            start=iso(low),
            end=iso(high),
            with_upn=other,
            events=matched[:limit],
            truncated=len(matched) > limit,
        )

    # ─────────────────────────── mail ───────────────────────────

    async def search_mail_with(self, token: str, with_upn: str, since: str, limit: int = 10) -> Mail:
        now = self.clock()
        other = address_of(with_upn)
        floor = self._since(since)
        limit = _limit(limit)
        params = {
            "$search": f'"participants:{other}"',
            "$select": "subject,receivedDateTime,from,sender,toRecipients,ccRecipients,bccRecipients,"
            "bodyPreview,conversationId,isDraft",
            "$top": str(PAGE),
        }
        threads: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
        async for page in self.graph.pages(token, "/me/messages", params, max_pages=MAX_PAGES):
            older = 0
            for raw in page:
                received = graph_moment(raw.get("receivedDateTime"))
                if received is None or received < floor:
                    older += 1
                    continue
                if raw.get("isDraft") is True:
                    continue
                people = _addresses(
                    _person(raw.get("from")),
                    _person(raw.get("sender")),
                    *(_person(r) for key in ("toRecipients", "ccRecipients", "bccRecipients") for r in _list(raw, key)),
                )
                if other not in people:  # Graph's search is fuzzy: only an exchange they take part in
                    continue
                key = raw.get("conversationId") if isinstance(raw.get("conversationId"), str) else id(raw)
                threads.setdefault(str(key), []).append((received, raw))
            if page and older == len(page):
                break  # Graph returns the newest first: the rest is older than `since`
        latest = sorted(
            ((max(messages, key=lambda m: m[0]), len(messages)) for messages in threads.values()),
            key=lambda item: item[0][0],
            reverse=True,
        )
        return Mail(
            as_of=iso(now),
            with_upn=other,
            since=iso(floor),
            threads=[
                MailThread(
                    subject=raw.get("subject") if isinstance(raw.get("subject"), str) else None,
                    date=iso(received),
                    sender=_person(raw.get("from")) or _person(raw.get("sender")),
                    snippet=snippet(raw.get("bodyPreview")),
                    message_count=count,
                )
                for (received, raw), count in latest[:limit]
            ],
            truncated=len(latest) > limit,
        )

    # ─────────────────────────── chats ───────────────────────────

    async def search_chats_with(self, token: str, with_upn: str, since: str, limit: int = 10) -> Chats:
        now = self.clock()
        other = address_of(with_upn)
        floor = self._since(since)
        limit = _limit(limit)
        params = {
            "$expand": "members",
            "$top": str(PAGE),
            "$orderby": "lastMessagePreview/createdDateTime desc",
        }
        candidates: list[tuple[dict[str, Any], dict[str, str]]] = []
        async for page in self.graph.pages(token, "/me/chats", params, max_pages=MAX_PAGES):
            for raw in page:
                if raw.get("chatType") not in ("oneOnOne", "group"):
                    continue  # a meeting's chat is the meeting's, not an exchange between the two
                updated = graph_moment(raw.get("lastUpdatedDateTime"))
                if updated is not None and updated < floor:
                    continue
                members = _members(raw)
                if other not in members.values():
                    continue
                candidates.append((raw, members))
        found: list[tuple[datetime, Chat]] = []
        for raw, members in candidates:
            if len(found) == limit + 1:
                break
            chat_id = raw.get("id")
            if not isinstance(chat_id, str) or not chat_id:
                continue
            body = await self.graph.get(
                token,
                f"/me/chats/{quote(chat_id, safe='')}/messages",
                {"$top": str(PAGE), "$orderby": "createdDateTime desc"},
            )
            message = _latest_message(body.get("value"), floor)
            if message is None:
                continue
            created, latest = message
            sender_id, sender_name = _sender(latest)
            found.append(
                (
                    created,
                    Chat(
                        chat_type="one_on_one" if raw.get("chatType") == "oneOnOne" else "group",
                        topic=raw.get("topic") if isinstance(raw.get("topic"), str) else None,
                        member_count=len(_list(raw, "members")),
                        date=iso(created),
                        sender=Person(name=sender_name, address=members.get(sender_id or "")),
                        snippet=snippet(
                            (latest.get("body") or {}).get("content"),
                            markup=(latest.get("body") or {}).get("contentType") == "html",
                        ),
                    ),
                )
            )
        found.sort(key=lambda item: item[0], reverse=True)
        return Chats(
            as_of=iso(now),
            with_upn=other,
            since=iso(floor),
            chats=[chat for _, chat in found[:limit]],
            truncated=len(found) > limit,
        )


# ─────────────────────────── Graph shapes ───────────────────────────


def _list(raw: dict[str, Any], key: str) -> list[Any]:
    value = raw.get(key)
    return value if isinstance(value, list) else []


class _Event(BaseModel):
    subject: str | None
    start: str | None
    end: str | None
    is_all_day: bool
    organizer: Person | None
    all_attendees: list[Attendee]
    is_organizer: bool
    is_online_meeting: bool
    is_cancelled: bool

    def public(self, one_to_one: bool) -> Event:
        return Event(
            subject=self.subject,
            start=self.start,
            end=self.end,
            is_all_day=self.is_all_day,
            organizer=self.organizer,
            attendees=self.all_attendees[:MAX_ATTENDEES],
            attendee_count=len(self.all_attendees),
            one_to_one=one_to_one,
            is_organizer=self.is_organizer,
            is_online_meeting=self.is_online_meeting,
            is_cancelled=self.is_cancelled,
        )


def _event(raw: dict[str, Any]) -> _Event:
    attendees: list[Attendee] = []
    for item in _list(raw, "attendees"):
        person = _person(item)
        if person is None:
            continue
        status = item.get("status")
        response = status.get("response") if isinstance(status, dict) else None
        kind = item.get("type")
        attendees.append(
            Attendee(
                name=person.name,
                address=person.address,
                response=response if isinstance(response, str) else None,
                type=kind if isinstance(kind, str) else None,
            )
        )
    start, end = graph_moment(raw.get("start")), graph_moment(raw.get("end"))
    return _Event(
        subject=raw.get("subject") if isinstance(raw.get("subject"), str) else None,
        start=iso(start) if start else None,
        end=iso(end) if end else None,
        is_all_day=raw.get("isAllDay") is True,
        organizer=_person(raw.get("organizer")),
        all_attendees=attendees,
        is_organizer=raw.get("isOrganizer") is True,
        is_online_meeting=raw.get("isOnlineMeeting") is True,
        is_cancelled=raw.get("isCancelled") is True,
    )


def _members(chat: dict[str, Any]) -> dict[str, str]:
    """userId -> address, for the members Graph gives an address."""
    found: dict[str, str] = {}
    for member in _list(chat, "members"):
        if not isinstance(member, dict):
            continue
        email, user = member.get("email"), member.get("userId")
        if isinstance(email, str) and email and isinstance(user, str):
            found[user] = email.lower()
    return found


def _latest_message(value: Any, floor: datetime) -> tuple[datetime, dict[str, Any]] | None:
    """The newest message a person wrote since `floor`: no system event, nothing deleted."""
    best: tuple[datetime, dict[str, Any]] | None = None
    for message in value if isinstance(value, list) else []:
        if not isinstance(message, dict) or message.get("messageType") != "message" or message.get("deletedDateTime"):
            continue
        created = graph_moment(message.get("createdDateTime"))
        if created is None or created < floor:
            continue
        if best is None or created > best[0]:
            best = (created, message)
    return best


def _sender(message: dict[str, Any]) -> tuple[str | None, str | None]:
    sender = message.get("from")
    user = sender.get("user") if isinstance(sender, dict) else None
    if not isinstance(user, dict):
        return None, None
    user_id, name = user.get("id"), user.get("displayName")
    return (user_id if isinstance(user_id, str) else None, name if isinstance(name, str) else None)
