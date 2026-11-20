# SPDX-License-Identifier: Apache-2.0
"""Work item keys: what names a request, resolvable back to it with no state of the server's own.

- `mail:<id>`: a message of the shared mailbox, by its Graph **immutable** id (every mail call sends
  `Prefer: IdType="ImmutableId"`), which does not change when the message moves between folders. An
  immutable id is about 70 characters in practice; the tracker profile bounds a key to 128, so an id longer
  than 123 characters is never made into a key (`tracker_list_candidates` skips it and says so).
- `teams:<conversationId>;<activityId>`: a channel message, as the Bot Connector names it. In a channel,
  `conversationId` is `19:<channel>@thread.tacv2;messageid=<root message id>`, so the key names the
  channel, the thread's root and the message: everything Graph needs to read it and the Bot Connector to
  answer in the thread. About 90 characters; never longer than 128 (refused otherwise).

The Bot Connector's service URL is NOT in the key (it would not fit): see `tracker.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_KEY = 128
MAIL = "mail:"
TEAMS = "teams:"
#: Graph ids are base64 (URL-safe in practice); `/` and `+` are accepted and percent-encoded in paths.
_MAIL_ID = re.compile(r"^[A-Za-z0-9+/=_-]{1,123}$")
_CONVERSATION = re.compile(r"^(19:[A-Za-z0-9_-]{1,64}@thread\.(?:tacv2|skype))(?:;messageid=(\d{1,20}))?$")
_ACTIVITY = re.compile(r"^\d{1,20}$")


@dataclass(frozen=True, slots=True)
class MailKey:
    message_id: str

    @property
    def key(self) -> str:
        return f"{MAIL}{self.message_id}"


@dataclass(frozen=True, slots=True)
class TeamsKey:
    conversation_id: str
    activity_id: str
    channel_id: str
    #: The thread's root message: the activity itself when it is the post that started the thread.
    root_id: str

    @property
    def key(self) -> str:
        return f"{TEAMS}{self.conversation_id};{self.activity_id}"

    @property
    def is_root(self) -> bool:
        return self.root_id == self.activity_id


def mail_key(message_id: str) -> MailKey | None:
    """The key of a message, or `None` when its id is not one this server can make a key of."""
    return MailKey(message_id) if _MAIL_ID.match(message_id) else None


def teams_key(conversation_id: str, activity_id: str) -> TeamsKey | None:
    found = _CONVERSATION.match(conversation_id)
    if found is None or not _ACTIVITY.match(activity_id):
        return None
    candidate = TeamsKey(conversation_id, activity_id, found.group(1), found.group(2) or activity_id)
    return candidate if len(candidate.key) <= MAX_KEY else None


def parse_key(key: str) -> MailKey | TeamsKey | None:
    """`None` for anything that is not a key of this server: another tracker's key, a child work item's
    (`DESK-1.1`), a malformed or over-long one."""
    if len(key) > MAX_KEY:
        return None
    if key.startswith(MAIL):
        return mail_key(key[len(MAIL) :])
    if key.startswith(TEAMS):
        conversation, separator, activity = key[len(TEAMS) :].rpartition(";")
        return teams_key(conversation, activity) if separator else None
    return None
