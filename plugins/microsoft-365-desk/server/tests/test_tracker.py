# SPDX-License-Identifier: Apache-2.0
"""The tracker profile (TRACKER_PROFILE.md), through MCP, against the fake tenant: every call made with the
arguments the core's adapter sends, and every answer checked against the core's profile when the core is
checked out (`conftest.PROFILE_SCHEMA`)."""

from __future__ import annotations

import pytest

from choregos_m365_desk.app import Runtime, build_server
from choregos_m365_desk.config import FAKE_CHANNEL_ID, Settings
from choregos_m365_desk.fake import (
    CONVERSATION_ID,
    MAIL_HTML,
    MAIL_PLAIN,
    MAIL_TAKEN,
    REPLY_ID,
    ROOT_ID,
    FakeMicrosoft,
)
from choregos_m365_desk.keys import parse_key
from choregos_m365_desk.tracker import status_property

from .conftest import SKIP_WITHOUT_CORE, Mcp, Profile, Tracker, no_sleep

PLAIN = f"mail:{MAIL_PLAIN}"
HTML = f"mail:{MAIL_HTML}"
TAKEN = f"mail:{MAIL_TAKEN}"
ROOT = f"teams:{CONVERSATION_ID};{ROOT_ID}"
REPLY = f"teams:{CONVERSATION_ID};{REPLY_ID}"
#: Keys that are not this desk's: a child work item of a fan-out, another tracker's, malformed ones.
FOREIGN = ["DESK-1.1", "INC0010001", "mail:", "mail:has space", "teams:19:x@thread.tacv2", "teams:nope;123"]


def test_the_core_profile_is_there(profile: Profile | None) -> None:
    """Said, not silent: without the core's checkout the answers are not checked against its profile."""
    if profile is None:
        pytest.skip(SKIP_WITHOUT_CORE)


def no_mention_of(text: str) -> bool:
    return "<at" not in text and "Desk " not in text


async def test_fetch_a_plain_mail(tracker: Tracker, fake: FakeMicrosoft) -> None:
    item = await tracker("tracker_fetch_item", key=PLAIN)
    assert item["key"] == PLAIN
    assert item["title"] == "VPN token expired"
    assert item["body"] == "My VPN token expired this morning, I cannot connect.\nCan you reset it?"
    assert item["author"] == "alex.durand@example.test"
    assert item["url"].startswith("https://outlook.office365.com/owa/")
    assert (item["state"], item["labels"], item["item_type"]) == ("New", ["agent-ready"], "mail")
    assert item["fields"]["has_attachments"] is False
    # Every mail call asks Graph for immutable ids.
    graph = [r for r in fake.requests if r.url.host == "graph.microsoft.com"]
    assert graph and all(r.headers.get("Prefer") == 'IdType="ImmutableId"' for r in graph)


async def test_an_html_mail_is_plain_text_without_markup_scripts_or_remote_content(tracker: Tracker) -> None:
    item = await tracker("tracker_fetch_item", key=HTML)
    assert item["body"] == "Hello desk,\n\nThe printer on floor 3 prints blank pages & jams.\n\nThanks,\nSam"
    for absent in ("<", "color", "alert", "pixel", "Ignored", "&amp;"):
        assert absent not in item["body"], absent
    assert item["fields"]["has_attachments"] is True


async def test_a_categorised_mail_carries_its_state_and_no_start_label(tracker: Tracker) -> None:
    item = await tracker("tracker_fetch_item", key=TAKEN)
    assert (item["state"], item["labels"]) == ("In progress", [])


async def test_long_texts_are_clipped(tracker: Tracker, fake: FakeMicrosoft) -> None:
    fake.add_message("AAkAlong", "S" * 400, "x@example.test", "b" * 9000)
    item = await tracker("tracker_fetch_item", key="mail:AAkAlong")
    assert (len(item["title"]), len(item["body"])) == (300, 8000)
    assert item["body"].endswith("…")


async def test_fetch_a_teams_message_and_a_reply_without_the_bot_mention(tracker: Tracker) -> None:
    root = await tracker("tracker_fetch_item", key=ROOT)
    assert root["title"] == root["body"] == "My second screen flickers since the update."
    assert (root["author"], root["item_type"], root["labels"]) == ("Camille Martin", "teams", ["agent-ready"])
    assert root["url"].startswith("https://teams.microsoft.com/l/message/")
    assert root["fields"]["root_id"] == root["fields"]["activity_id"] == ROOT_ID
    reply = await tracker("tracker_fetch_item", key=REPLY)
    assert (reply["body"], reply["author"]) == ("Same for me on floor 2.", "Alex Durand")
    assert (reply["fields"]["root_id"], reply["fields"]["activity_id"]) == (ROOT_ID, REPLY_ID)
    assert no_mention_of(root["body"]) and no_mention_of(reply["body"])


@pytest.mark.parametrize("key", FOREIGN)
async def test_fetching_a_key_that_is_not_this_desks_is_refused_not_crashed(mcp: Mcp, key: str) -> None:
    assert "refused:" in await mcp.error("tracker_fetch_item", key=key)


async def test_fetching_a_message_that_is_gone_is_refused(mcp: Mcp) -> None:
    assert "refused: no message" in await mcp.error("tracker_fetch_item", key="mail:AAkAgone")
    other_root = f"teams:{FAKE_CHANNEL_ID};messageid=1;1"
    assert "refused: no message" in await mcp.error("tracker_fetch_item", key=other_root)
    unknown_channel = "teams:19:0000@thread.tacv2;messageid=1;1"
    assert "not one this desk answers" in await mcp.error("tracker_fetch_item", key=unknown_channel)


async def test_list_candidates_is_the_untaken_inbox_newest_first(tracker: Tracker) -> None:
    assert await tracker("tracker_list_candidates", limit=50) == {"keys": [HTML, PLAIN]}
    assert await tracker("tracker_list_candidates", limit=1) == {"keys": [HTML]}


async def test_list_candidates_pages_within_graph_and_skips_ids_too_long_for_a_key(
    tracker: Tracker, fake: FakeMicrosoft
) -> None:
    for n in range(60):
        fake.add_message(f"AAkApage{n:03d}", f"n{n}", "x@example.test", "b", received=f"2026-11-17T{n % 24:02d}:00:00Z")
    fake.add_message("A" * 124, "too long", "x@example.test", "b", received="2026-11-19T00:00:00Z")
    found = await tracker("tracker_list_candidates", limit=200)
    assert len(found["keys"]) == 62
    assert found["keys"][:2] == [HTML, PLAIN]
    assert (found["skipped"], "longer than 128" in found["reason"]) == (1, True)
    assert all(len(key) <= 128 and parse_key(key) is not None for key in found["keys"])


async def test_a_next_page_outside_graph_is_refused(mcp: Mcp, fake: FakeMicrosoft) -> None:
    for n in range(60):
        fake.add_message(f"AAkApage{n:03d}", f"n{n}", "x@example.test", "b")
    fake.foreign_next_link = True
    assert "outside Graph" in await mcp.error("tracker_list_candidates", limit=200)
    assert not [r for r in fake.requests if r.url.host == "graph.example.test"]


async def test_without_a_mailbox_there_is_no_candidate(fake: FakeMicrosoft, profile: Profile | None) -> None:
    settings = Settings.from_env({"M365_DESK_FAKE": "1"}).model_copy(update={"mailbox": None})
    tracker = Tracker(Mcp(build_server(Runtime(settings, fake=fake))), profile)
    found = await tracker("tracker_list_candidates", limit=50)
    assert found["keys"] == [] and "no shared mailbox" in found["reason"]


async def test_a_throttled_graph_is_retried_after_retry_after(tracker: Tracker, fake: FakeMicrosoft) -> None:
    fake.throttle = 2
    assert (await tracker("tracker_fetch_item", key=PLAIN))["title"] == "VPN token expired"
    fake.throttle = 3  # more than the two retries
    assert "rate_limited" in await tracker.mcp.error("tracker_fetch_item", key=PLAIN)


async def test_another_mailbox_is_refused_as_rbac_refuses_it(fake: FakeMicrosoft) -> None:
    settings = Settings.from_env({"M365_DESK_FAKE": "1"}).model_copy(update={"mailbox": "ceo@example.test"})
    mcp = Mcp(build_server(Runtime(settings, fake=fake, sleep=no_sleep)))
    assert "forbidden: Microsoft Graph refused" in await mcp.error("tracker_list_candidates", limit=5)


async def test_create_item_is_refused(mcp: Mcp) -> None:
    arguments = {"title": "t", "body": "b", "labels": [], "assignees": [], "fields": {}, "action_key": "platform:1"}
    assert "refused: this desk does not create" in await mcp.error("tracker_create_item", **arguments)


async def test_set_state_is_a_category_kept_beside_the_others(tracker: Tracker, fake: FakeMicrosoft) -> None:
    changed = await tracker("tracker_set_state", key=TAKEN, status="Waiting for review", label="x", remove_labels=[])
    assert changed == {"key": TAKEN, "changed": True, "state": "Waiting for review", "ignored": ["label"]}
    assert fake.messages[MAIL_TAKEN].categories == ["Blue category", "Choregos: Waiting for review"]
    again = await tracker("tracker_set_state", key=TAKEN, status="Waiting for review", label=None, remove_labels=[])
    assert again["changed"] is False
    nothing = await tracker("tracker_set_state", key=PLAIN, status=None, label=None, remove_labels=[])
    assert (nothing["changed"], nothing["state"]) == (False, None)  # nothing to write: nothing read either
    assert (await tracker("tracker_list_candidates", limit=50))["keys"] == [HTML, PLAIN]
    await tracker("tracker_set_state", key=PLAIN, status="In progress", label=None, remove_labels=[])
    assert (await tracker("tracker_list_candidates", limit=50))["keys"] == [HTML]
    assert (await tracker("tracker_fetch_item", key=PLAIN))["labels"] == []


async def test_a_status_never_breaks_a_category(tracker: Tracker, fake: FakeMicrosoft) -> None:
    await tracker("tracker_set_state", key=PLAIN, status="a,b;\nc" + "x" * 100, label=None, remove_labels=[])
    category = fake.messages[MAIL_PLAIN].categories[-1]
    assert category.startswith("Choregos: a b") and "," not in category and "\n" not in category
    assert len(category) <= len("Choregos: ") + 60


async def test_upsert_status_keeps_the_status_out_of_the_requesters_sight(
    tracker: Tracker, fake: FakeMicrosoft
) -> None:
    written = await tracker("tracker_upsert_status", key=PLAIN, markdown="**Triage**", marker="choregos:status")
    assert written == {"key": PLAIN, "written": True}
    assert fake.messages[MAIL_PLAIN].properties == {status_property("choregos:status"): "**Triage**"}
    same = await tracker("tracker_upsert_status", key=PLAIN, markdown="**Triage**", marker="choregos:status")
    assert same["written"] is False
    assert (await tracker("tracker_upsert_status", key=PLAIN, markdown="Review", marker="choregos:status"))["written"]
    assert fake.sent_mail == []
    assert not [r for r in fake.requests if r.url.path.endswith(("/send", "/createReply"))]


async def test_close_sets_done_once_and_sends_nothing(tracker: Tracker, fake: FakeMicrosoft) -> None:
    closed = await tracker("tracker_close", key=TAKEN, close_notes="Delivered.", close_code=None)
    assert closed == {"key": TAKEN, "closed": True, "state": "Done"}
    assert fake.messages[MAIL_TAKEN].categories == ["Blue category", "Choregos: Done"]
    again = await tracker("tracker_close", key=TAKEN, close_notes="Delivered.", close_code=None)
    assert again == {"key": TAKEN, "closed": False, "state": "Done"}
    assert fake.sent_mail == []


async def test_teams_state_writes_are_successful_no_ops(tracker: Tracker, fake: FakeMicrosoft) -> None:
    before = len(fake.requests)
    assert (await tracker("tracker_set_state", key=ROOT, status="In progress", label=None, remove_labels=[]))[
        "changed"
    ] is False
    assert (await tracker("tracker_upsert_status", key=ROOT, markdown="s", marker="status"))["written"] is False
    assert (await tracker("tracker_close", key=ROOT, close_notes=None, close_code=None))["closed"] is False
    assert len(fake.requests) == before


@pytest.mark.parametrize("key", FOREIGN)
async def test_every_mirrored_write_on_a_key_that_is_not_this_desks_is_a_no_op(
    tracker: Tracker, fake: FakeMicrosoft, key: str
) -> None:
    """A fan-out's children mirror their state to the parent's tracker: none of these may fail."""
    state = await tracker("tracker_set_state", key=key, status="In progress", label=None, remove_labels=[])
    assert (state["key"], state["changed"]) == (key, False)
    assert (await tracker("tracker_upsert_status", key=key, markdown="s", marker="status"))["written"] is False
    assert (await tracker("tracker_close", key=key, close_notes=None, close_code=None))["closed"] is False
    comment = await tracker("tracker_comment", key=key, markdown="Run started.", action_key="platform:abc")
    assert comment["written"] is False
    assert fake.requests == []


async def test_a_platform_comment_on_a_mail_sends_nothing(tracker: Tracker, fake: FakeMicrosoft) -> None:
    comment = await tracker("tracker_comment", key=PLAIN, markdown="We reset your token.", action_key="platform:c1")
    assert comment["written"] is False
    assert fake.sent_mail == [] and fake.requests == []


async def test_mirrored_writes_on_a_channel_no_longer_configured_are_no_ops(
    fake: FakeMicrosoft, profile: Profile | None
) -> None:
    settings = Settings.from_env({"M365_DESK_FAKE": "1"}).model_copy(update={"mailbox": None})
    tracker = Tracker(Mcp(build_server(Runtime(settings, fake=fake))), profile)
    unknown_channel = "teams:19:0000@thread.tacv2;messageid=1;1"
    for key in (PLAIN, unknown_channel):
        assert (await tracker("tracker_close", key=key, close_notes=None, close_code=None))["closed"] is False
        assert (await tracker("tracker_comment", key=key, markdown="m", action_key="platform:k"))["written"] is False
    assert fake.requests == []


# ─────────────────────────── what the core mirrors, and only that ───────────────────────────


async def test_a_state_mirrored_by_its_label_alone_is_a_category(tracker: Tracker, fake: FakeMicrosoft) -> None:
    """The core's `mirror_state` always sends a label (`choregos:<state>` when the workflow names none) and a
    status only when the workflow maps one: either way the message the platform took is categorised."""
    by_label = await tracker("tracker_set_state", key=PLAIN, status=None, label="choregos:triage", remove_labels=[])
    assert by_label == {"key": PLAIN, "changed": True, "state": "triage", "ignored": []}
    assert fake.messages[MAIL_PLAIN].categories == ["Choregos: triage"]
    named = await tracker("tracker_set_state", key=PLAIN, status=None, label="Waiting for review", remove_labels=[])
    assert (named["state"], fake.messages[MAIL_PLAIN].categories) == (
        "Waiting for review",
        ["Choregos: Waiting for review"],
    )
    assert (await tracker("tracker_list_candidates", limit=50))["keys"] == [HTML]


async def test_a_mirrored_write_with_nothing_to_write_calls_nobody(tracker: Tracker, fake: FakeMicrosoft) -> None:
    state = await tracker("tracker_set_state", key=PLAIN, status=None, label=None, remove_labels=[])
    assert (state["changed"], await tracker("tracker_upsert_status", key=PLAIN, markdown="  ", marker="s")) == (
        False,
        {"key": PLAIN, "written": False},
    )
    assert fake.requests == []


async def test_a_mirrored_write_on_a_message_that_is_gone_is_a_no_op(tracker: Tracker, fake: FakeMicrosoft) -> None:
    gone = "mail:AAkAgone"
    assert (await tracker("tracker_set_state", key=gone, status="x", label=None, remove_labels=[]))["changed"] is False
    assert (await tracker("tracker_upsert_status", key=gone, markdown="s", marker="status"))["written"] is False
    assert (await tracker("tracker_close", key=gone, close_notes=None, close_code=None))["closed"] is False
    fake.add_message("AAkAvanishing", "s", "x@example.test", "b")
    del fake.messages["AAkAvanishing"]
    assert (await tracker("tracker_close", key="mail:AAkAvanishing", close_notes=None, close_code=None))[
        "closed"
    ] is False


async def test_auto_replies_reports_and_follow_ups_are_not_candidates(tracker: Tracker, fake: FakeMicrosoft) -> None:
    taken = fake.messages[MAIL_TAKEN].render(None)["conversationId"]
    fake.add_message(
        "AAkAoof", "Automatic reply: away", "x@example.test", "b", message_class="IPM.Note.Rules.OofTemplate.Microsoft"
    )
    fake.add_message("AAkAndr", "Undeliverable", "postmaster@example.test", "b", message_class="REPORT.IPM.Note.NDR")
    fake.add_message(
        "AAkAauto", "Ticket 42 received", "noreply@vendor.test", "b", headers={"Auto-Submitted": "auto-replied"}
    )
    fake.add_message("AAkAhuman", "Hello", "y@example.test", "b", headers={"Auto-Submitted": "no"})
    fake.add_message("AAkAfollow", "RE: New laptop for Kim", "kim.lee@example.test", "Thanks!", conversation=taken)
    assert (await tracker("tracker_list_candidates", limit=50))["keys"] == [HTML, PLAIN, "mail:AAkAhuman"]


async def test_a_follow_up_to_a_thread_taken_long_ago_is_found_in_the_whole_mailbox(
    tracker: Tracker, fake: FakeMicrosoft
) -> None:
    """The taken message may have left the Inbox (archived to another folder): the mailbox is asked."""
    fake.messages[MAIL_TAKEN].folder = "archive"
    taken = fake.messages[MAIL_TAKEN].render(None)["conversationId"]
    fake.add_message("AAkAfollow", "RE: New laptop for Kim", "kim.lee@example.test", "Thanks!", conversation=taken)
    assert (await tracker("tracker_list_candidates", limit=50))["keys"] == [HTML, PLAIN]


# ─────────────────────────── key bounds ───────────────────────────


def test_a_mail_key_leaves_room_for_a_fan_out_childs_suffix() -> None:
    """A child of `mail:<id>` is `mail:<id>.<n>`: with n up to 99 it must still fit the profile's 128."""
    assert parse_key("mail:" + "A" * 119) is not None
    assert parse_key("mail:" + "A" * 120) is None
    assert len("mail:" + "A" * 119 + ".99") <= 128


async def test_an_id_too_long_for_a_childs_key_is_skipped(tracker: Tracker, fake: FakeMicrosoft) -> None:
    fake.add_message("B" * 120, "too long for a child", "x@example.test", "b", received="2026-11-19T00:00:00Z")
    found = await tracker("tracker_list_candidates", limit=50)
    assert (found["keys"], found["skipped"]) == ([HTML, PLAIN], 1)


@pytest.mark.parametrize("key", ["x" * 300, "DESK-" + "1" * 200, "mail:" + "A" * 200])
async def test_a_mirrored_write_on_an_over_long_foreign_key_still_answers_within_the_profile(
    mcp: Mcp, profile: Profile | None, key: str
) -> None:
    answers = {
        "tracker_set_state": await mcp.call("tracker_set_state", key=key, status="s", label=None, remove_labels=[]),
        "tracker_upsert_status": await mcp.call("tracker_upsert_status", key=key, markdown="s", marker="status"),
        "tracker_close": await mcp.call("tracker_close", key=key, close_notes=None, close_code=None),
        "tracker_comment": await mcp.call("tracker_comment", key=key, markdown="m", action_key="platform:k"),
    }
    for tool, answer in answers.items():
        assert len(answer["key"]) <= 128, tool
        if profile is not None:
            problems = list(profile.output_problems(tool, answer))
            assert not problems, (tool, problems)
