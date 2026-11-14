# SPDX-License-Identifier: Apache-2.0
"""The three reads on the fake tenant, called over HTTP as the platform calls them."""

from __future__ import annotations

import json

import pytest

from choregos_microsoft_365.fake import ALEX, CALENDAR_ONLY, CAMILLE, EXPIRED, ROOM, SAM
from choregos_microsoft_365.tools import SNIPPET_CHARS

from .conftest import PERSON, Mcp

TOOLS = ("list_events_with", "search_mail_with", "search_chats_with")
ARGUMENTS = {
    "list_events_with": {"within_hours": 48},
    "search_mail_with": {"with_upn": ALEX, "since": "2026-11-01"},
    "search_chats_with": {"with_upn": ALEX, "since": "2026-11-01"},
}


# ─────────────────────────── identity ───────────────────────────


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("person", [None, "", "   ", "Bearer", "bearer  ", "two words", "Bearer two words"])
def test_without_a_persons_token_the_call_is_refused_and_graph_never_called(
    mcp: Mcp, tool: str, person: str | None
) -> None:
    error = mcp.error(tool, person=person, **ARGUMENTS[tool])
    assert error.startswith("identity_unavailable: ")
    assert mcp.fake.requests == []


def test_the_persons_token_and_nothing_else_goes_to_graph(mcp: Mcp) -> None:
    mcp.call("list_events_with", within_hours=4)
    mcp.call("search_mail_with", with_upn=ALEX, since="2026-11-01")
    (request, *others) = mcp.fake.requests
    for sent in (request, *others):
        assert sent.headers["Authorization"] == f"Bearer {PERSON}"
        assert str(sent.url).startswith("https://graph.microsoft.com/v1.0/me/")


def test_a_token_graph_refuses_is_identity_unavailable_and_never_echoed(mcp: Mcp) -> None:
    error = mcp.error("list_events_with", person=EXPIRED, within_hours=4)
    assert error.startswith("identity_unavailable: ") and "InvalidAuthenticationToken" in error
    assert EXPIRED not in error


@pytest.mark.parametrize("tool", ["search_mail_with", "search_chats_with"])
def test_a_permission_the_tenant_did_not_grant_is_forbidden(mcp: Mcp, tool: str) -> None:
    assert mcp.call("list_events_with", person=CALENDAR_ONLY, within_hours=4)["events"]
    assert mcp.error(tool, person=CALENDAR_ONLY, **ARGUMENTS[tool]).startswith("forbidden: ")


# ─────────────────────────── calendar ───────────────────────────


def test_events_in_the_next_hours_oldest_first(mcp: Mcp) -> None:
    result = mcp.call("list_events_with", within_hours=48)
    assert result["source"] == "microsoft-graph"
    assert result["as_of"] == "2026-11-14T09:17:00Z"
    assert (result["start"], result["end"]) == ("2026-11-14T09:17:00Z", "2026-11-16T09:17:00Z")
    subjects = [e["subject"] for e in result["events"]]
    assert subjects == ["Alex / Camille", "Focus time", "Sam / Camille", "Roadmap review"]
    first = result["events"][0]
    assert (first["start"], first["end"]) == ("2026-11-14T11:00:00Z", "2026-11-14T11:30:00Z")
    assert first["organizer"] == {"name": "Camille Martin", "address": CAMILLE}
    assert {a["address"]: a["type"] for a in first["attendees"]} == {ALEX: "required", ROOM: "resource"}
    assert first["one_to_one"] is True  # the room is not a person
    assert result["truncated"] is False
    assert "body" not in json.dumps(result).lower()


def test_events_with_a_person(mcp: Mcp) -> None:
    result = mcp.call("list_events_with", within_hours=48, with_upn="Alex.Durand@Example.test")
    assert result["with_upn"] == ALEX
    assert [(e["subject"], e["one_to_one"]) for e in result["events"]] == [
        ("Alex / Camille", True),
        ("Roadmap review", False),
    ]


def test_one_to_one_only_excludes_a_meeting_of_three(mcp: Mcp) -> None:
    result = mcp.call("list_events_with", within_hours=48, with_upn=ALEX, one_to_one_only=True)
    assert [e["subject"] for e in result["events"]] == ["Alex / Camille"]
    # Without a person: every meeting of exactly two people, never one alone nor three.
    alone = mcp.call("list_events_with", within_hours=48, one_to_one_only=True)
    assert [e["subject"] for e in alone["events"]] == ["Alex / Camille", "Sam / Camille"]


def test_a_past_window_newest_first_and_limited(mcp: Mcp) -> None:
    result = mcp.call(
        "list_events_with",
        start="2026-11-01T00:00:00Z",
        end="2026-11-16",
        with_upn=ALEX,
        newest_first=True,
        limit=2,
    )
    assert [e["start"] for e in result["events"]] == ["2026-11-15T11:00:00Z", "2026-11-14T11:00:00Z"]
    assert result["truncated"] is True


def test_the_calendar_is_read_page_by_page(mcp: Mcp) -> None:
    mcp.call("list_events_with", within_hours=48)
    (request,) = mcp.fake.requests
    assert request.url.params["$top"] == "50"
    assert request.headers["Prefer"] == 'outlook.timezone="UTC"'
    assert "body" not in request.url.params["$select"]


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        ({}, "give `start` and `end`"),
        ({"start": "2026-11-14T00:00:00Z"}, "give `start` and `end`"),
        ({"within_hours": 4, "start": "2026-11-14", "end": "2026-11-15"}, "not both"),
        ({"within_hours": 0}, "between 1 and"),
        ({"within_hours": 24 * 93}, "between 1 and"),
        ({"start": "2026-11-15", "end": "2026-11-14"}, "after `start`"),
        ({"start": "2026-01-01", "end": "2026-11-14"}, "at most 92 days"),
        ({"start": "next monday", "end": "2026-11-14"}, "not an ISO 8601"),
        ({"within_hours": 4, "with_upn": 'x" OR "y'}, "with_upn"),
        ({"within_hours": 4, "limit": 21}, "between 1 and 20"),
    ],
)
def test_arguments_are_checked_before_graph(mcp: Mcp, arguments: dict[str, object], says: str) -> None:
    error = mcp.error("list_events_with", **arguments)
    assert error.startswith("invalid_arguments: ") and says in error
    assert mcp.fake.requests == []


# ─────────────────────────── mail ───────────────────────────


def test_mail_threads_with_a_person(mcp: Mcp) -> None:
    result = mcp.call("search_mail_with", with_upn=ALEX, since="2026-11-01")
    assert (result["source"], result["with_upn"], result["since"]) == (
        "microsoft-graph",
        ALEX,
        "2026-11-01T00:00:00Z",
    )
    (thread,) = result["threads"]
    assert thread["subject"] == "RE: Project timeline"
    assert thread["date"] == "2026-11-13T09:00:00Z"
    assert thread["sender"] == {"name": "Camille Martin", "address": CAMILLE}
    assert thread["message_count"] == 2
    assert result["truncated"] is False


def test_a_mail_where_the_person_takes_no_part_is_never_returned(mcp: Mcp) -> None:
    """Graph's search is fuzzy: Sam's mail that names Alex comes back from it, and is not returned."""
    result = mcp.call("search_mail_with", with_upn=ALEX, since="2026-11-01")
    searched = mcp.fake.requests[0].url.params["$search"]
    assert searched == f'"participants:{ALEX}"'
    assert "Budget" not in json.dumps(result)
    assert "budget is cut" not in json.dumps(result)
    # Sam's own mail is Sam's to see, with Sam.
    assert [t["subject"] for t in mcp.call("search_mail_with", with_upn=SAM, since="2026-11-01")["threads"]] == [
        "Budget",
        "RE: Project timeline",
    ]


def test_mail_is_minimised(mcp: Mcp) -> None:
    result = mcp.call("search_mail_with", with_upn=ALEX, since="2026-11-12T00:00:00Z")
    text = json.dumps(result)
    assert "attachment" not in text.lower() and "Never asked for" not in text
    selected = mcp.fake.requests[0].url.params["$select"].split(",")
    assert "body" not in selected and "hasAttachments" not in selected and "attachments" not in selected
    (thread,) = result["threads"]
    assert set(thread) == {"subject", "date", "sender", "snippet", "message_count"}


def test_a_long_preview_is_cut_at_300_characters(mcp: Mcp) -> None:
    mailbox = mcp.fake.mailboxes[CAMILLE]
    mailbox.messages = [m for m in mailbox.messages if m["subject"] == "Project timeline"]
    (thread,) = mcp.call("search_mail_with", with_upn=ALEX, since="2026-11-01")["threads"]
    assert len(thread["snippet"]) == SNIPPET_CHARS and thread["snippet"].endswith("…")


def test_since_bounds_mail_and_drafts_are_skipped(mcp: Mcp) -> None:
    result = mcp.call("search_mail_with", with_upn=ALEX, since="2026-09-01")
    assert [t["subject"] for t in result["threads"]] == ["RE: Project timeline", "Old thread"]
    assert "Draft" not in json.dumps(result)


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        ({"with_upn": "alex", "since": "2026-11-01"}, "with_upn"),
        ({"with_upn": f'{ALEX}" OR "x', "since": "2026-11-01"}, "with_upn"),
        ({"with_upn": ALEX, "since": "2026-12-01"}, "in the future"),
        ({"with_upn": ALEX, "since": "2025-01-01"}, "at most 366 days"),
        ({"with_upn": ALEX, "since": "2026-11-01", "limit": 0}, "between 1 and 20"),
    ],
)
def test_mail_arguments_are_checked_before_graph(mcp: Mcp, arguments: dict[str, object], says: str) -> None:
    error = mcp.error("search_mail_with", **arguments)
    assert error.startswith("invalid_arguments: ") and says in error
    assert mcp.fake.requests == []


# ─────────────────────────── chats ───────────────────────────


def test_chats_with_a_person_one_to_one_and_group(mcp: Mcp) -> None:
    result = mcp.call("search_chats_with", with_upn=ALEX, since="2026-11-01")
    assert result["source"] == "microsoft-graph"
    one, group = result["chats"]
    assert one == {
        "chat_type": "one_on_one",
        "topic": None,
        "member_count": 2,
        "date": "2026-11-14T08:00:00Z",
        "sender": {"name": "Camille Martin", "address": CAMILLE},
        "snippet": "See you Thursday & thanks!",
    }
    assert (group["chat_type"], group["topic"], group["member_count"]) == ("group", "Launch", 3)
    assert group["sender"] == {"name": "Sam Petit", "address": SAM}
    assert group["snippet"] == "Launch moves to Monday."  # the later system event is not a message
    text = json.dumps(result)
    assert "Hiring" not in text  # Alex is not a member
    assert "Notes" not in text  # a meeting's chat
    assert "secret-plan" not in text and "attachment" not in text.lower()
    assert result["truncated"] is False


def test_chats_since_and_limit(mcp: Mcp) -> None:
    later = mcp.call("search_chats_with", with_upn=ALEX, since="2026-11-14T07:00:00Z")
    assert [c["chat_type"] for c in later["chats"]] == ["one_on_one"]
    first = mcp.call("search_chats_with", with_upn=ALEX, since="2026-11-01", limit=1)
    assert [c["chat_type"] for c in first["chats"]] == ["one_on_one"] and first["truncated"] is True


def test_another_persons_mailbox_is_theirs(mcp: Mcp) -> None:
    """The fake serves the token's own mailbox: Alex's token reads nothing of Camille's."""
    assert mcp.call("search_chats_with", person=f"fake:{ALEX}", with_upn=CAMILLE, since="2026-11-01")["chats"] == []
