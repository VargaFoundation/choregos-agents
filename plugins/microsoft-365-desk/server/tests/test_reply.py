# SPDX-License-Identifier: Apache-2.0
"""`reply` (and `tracker_comment`): the requester is answered in the original thread, once per action."""

from __future__ import annotations

import json

import pytest

from choregos_m365_desk.app import Runtime, build_server
from choregos_m365_desk.config import Settings
from choregos_m365_desk.fake import (
    CONVERSATION_ID,
    MAIL_PLAIN,
    REPLY_ID,
    ROOT_ID,
    FakeMicrosoft,
    activity,
    message_id,
)
from choregos_m365_desk.tracker import ACTION_PROPERTY, reference

from .conftest import Mcp, no_sleep

PLAIN = f"mail:{MAIL_PLAIN}"
ROOT = f"teams:{CONVERSATION_ID};{ROOT_ID}"
REPLY = f"teams:{CONVERSATION_ID};{REPLY_ID}"


def restarted(fake: FakeMicrosoft) -> Mcp:
    """The same tenant, a new process: nothing of the previous process's memory."""
    return Mcp(build_server(Runtime(Settings.from_env({"M365_DESK_FAKE": "1"}), fake=fake, sleep=no_sleep)))


# ─────────────────────────── mail ───────────────────────────


async def test_a_mail_reply_is_sent_once_per_action(mcp: Mcp, fake: FakeMicrosoft) -> None:
    arguments = {"key": PLAIN, "text": "Your token is reset.", "action_key": "a1", "work_item_key": PLAIN}
    first = await mcp.call("reply", **arguments)
    again = await mcp.call("reply", **arguments)
    after_a_restart = await restarted(fake).call("reply", **arguments)
    assert (first["sent"], again["sent"], after_a_restart["sent"]) == (True, False, False)
    assert first["id"] == again["id"] == after_a_restart["id"] == f"reply:{reference('a1')}"
    assert fake.sent_mail == [
        {
            "id": first["message_id"],
            "to": ["alex.durand@example.test"],
            "subject": "RE: VPN token expired",
            "body": "Your token is reset.",
        }
    ]
    assert len([r for r in fake.requests if r.url.path.endswith("/send")]) == 1


async def test_another_action_is_another_reply(mcp: Mcp, fake: FakeMicrosoft) -> None:
    await mcp.call("reply", key=PLAIN, work_item_key=PLAIN, text="One.", action_key="a1")
    await mcp.call("reply", key=PLAIN, work_item_key=PLAIN, text="Two.", action_key="a2")
    assert [m["body"] for m in fake.sent_mail] == ["One.", "Two."]


async def test_a_draft_left_by_an_interrupted_attempt_is_sent_not_duplicated(mcp: Mcp, fake: FakeMicrosoft) -> None:
    fake.add_message(message_id(900), "RE: VPN token expired", "it-desk@example.test", "Draft.", folder="drafts")
    fake.messages[message_id(900)].properties[ACTION_PROPERTY] = "a1"
    fake.messages[message_id(900)].to = ["alex.durand@example.test"]
    assert (await mcp.call("reply", key=PLAIN, work_item_key=PLAIN, text="Draft.", action_key="a1"))["sent"] is True
    assert [m["id"] for m in fake.sent_mail] == [message_id(900)]
    assert not [r for r in fake.requests if r.url.path.endswith("/createReply")]


async def test_the_mail_body_is_plain_text_never_markup(mcp: Mcp, fake: FakeMicrosoft) -> None:
    text = "<b>bold?</b> <img src=https://x.test/p.gif>"
    await mcp.call("reply", key=PLAIN, work_item_key=PLAIN, text=text, action_key="a'quoted")
    created = next(r for r in fake.requests if r.url.path.endswith("/createReply"))
    assert json.loads(created.content)["message"]["body"] == {"contentType": "text", "content": text}
    assert fake.sent_mail[0]["body"] == text


async def test_a_reply_to_a_mail_that_is_gone_is_refused(mcp: Mcp, fake: FakeMicrosoft) -> None:
    assert "refused: no message" in await mcp.error(
        "reply", key="mail:AAkAgone", work_item_key="mail:AAkAgone", text="Hi.", action_key="a1"
    )
    assert fake.sent_mail == []


# ─────────────────────────── what any reply must hold ───────────────────────────


@pytest.mark.parametrize(
    ("arguments", "why"),
    [
        ({"key": PLAIN, "text": "   "}, "needs a text"),
        ({"key": PLAIN, "text": "x" * 4001}, "at most 4000"),
        ({"key": "DESK-1.1", "text": "Hi."}, "not a work item of this desk"),
        ({"key": "teams:19:0000@thread.tacv2;messageid=1;1", "text": "Hi."}, "not one this desk answers"),
    ],
)
async def test_a_reply_that_breaks_a_rule_is_refused_before_anything_is_sent(
    mcp: Mcp, fake: FakeMicrosoft, arguments: dict[str, str], why: str
) -> None:
    error = await mcp.error("reply", action_key="a1", **{"work_item_key": arguments["key"], **arguments})
    assert "refused:" in error and why in error, error
    assert fake.sent_mail == [] and fake.connector_replies == []


@pytest.mark.parametrize(
    ("key", "work_item_key"),
    [
        pytest.param(PLAIN, None, id="no work item"),
        pytest.param(PLAIN, f"mail:{message_id(2)}", id="another request"),
        pytest.param(ROOT, PLAIN, id="another channel's request"),
        pytest.param(PLAIN, "DESK-1.1", id="another tracker's work item"),
        pytest.param(PLAIN, f"{PLAIN}.x", id="not a child's suffix"),
        pytest.param(PLAIN, f"{PLAIN}1", id="a longer id, not a child"),
        pytest.param(PLAIN, f"{PLAIN}.", id="an empty child segment"),
        pytest.param(f"{PLAIN}.1", f"{PLAIN}.1", id="a child key is no thread"),
    ],
)
async def test_a_reply_goes_to_its_own_work_items_thread_only(
    mcp: Mcp, fake: FakeMicrosoft, key: str, work_item_key: str | None
) -> None:
    arguments = {"key": key, "text": "Hi.", "action_key": "a1"}
    if work_item_key is not None:
        arguments["work_item_key"] = work_item_key
    assert "refused:" in await mcp.error("reply", **arguments)
    assert fake.sent_mail == [] and fake.connector_replies == []


@pytest.mark.parametrize("suffix", [".1", ".12", ".1.3", ".99.99.1"])
async def test_a_fan_out_child_answers_its_parents_requester(mcp: Mcp, fake: FakeMicrosoft, suffix: str) -> None:
    mail = await mcp.call("reply", key=PLAIN, text="Hi.", action_key="a1", work_item_key=f"{PLAIN}{suffix}")
    teams = await mcp.call("reply", key=ROOT, text="Hi.", action_key="a1", work_item_key=f"{ROOT}{suffix}")
    assert mail["sent"] and teams["sent"]


async def test_a_mail_reply_goes_to_the_sender_only_whatever_its_reply_to(mcp: Mcp, fake: FakeMicrosoft) -> None:
    """Graph's createReply addresses the original's Reply-To: the server names the sender instead."""
    fake.add_message("AAkAreplyto", "Help", "kim.lee@example.test", "b", reply_to=["attacker@evil.test"])
    key = "mail:AAkAreplyto"
    await mcp.call("reply", key=key, work_item_key=key, text="Done.", action_key="a1")
    assert [m["to"] for m in fake.sent_mail] == [["kim.lee@example.test"]]
    created = next(r for r in fake.requests if r.url.path.endswith("/createReply"))
    message = json.loads(created.content)["message"]
    assert message["toRecipients"] == [{"emailAddress": {"address": "kim.lee@example.test"}}]
    assert (message["ccRecipients"], message["bccRecipients"], message["replyTo"]) == ([], [], [])


async def test_the_reply_draft_is_born_marked_so_an_interrupted_attempt_is_resumed(
    mcp: Mcp, fake: FakeMicrosoft
) -> None:
    """One call creates the draft with its body and the action's mark: no unmarked draft can be left."""
    fake.fail_send = True
    arguments = {"key": PLAIN, "work_item_key": PLAIN, "text": "Your token is reset.", "action_key": "a1"}
    assert "upstream_error" in await mcp.error("reply", **arguments)
    [draft] = fake.folder("drafts")
    assert (draft.properties[ACTION_PROPERTY], draft.body, draft.content_type) == ("a1", "Your token is reset.", "text")
    assert not [r for r in fake.requests if r.method == "PATCH"]
    fake.fail_send = False
    assert (await mcp.call("reply", **arguments))["sent"] is True
    assert [m["id"] for m in fake.sent_mail] == [draft.id]
    assert len([r for r in fake.requests if r.url.path.endswith("/createReply")]) == 1


# ─────────────────────────── Teams ───────────────────────────


async def test_a_teams_reply_goes_to_the_thread_escaped(mcp: Mcp, fake: FakeMicrosoft) -> None:
    sent = await mcp.call(
        "reply", key=REPLY, work_item_key=REPLY, text='Fixed <script>x</script> & "done"\nBye', action_key="t1"
    )
    assert (sent["sent"], sent["channel"]) == (True, "teams")
    [made] = fake.connector_replies
    assert (made["region"], made["conversation"], made["reply_to"]) == ("teams", CONVERSATION_ID, REPLY_ID)
    posted = made["activity"]
    assert (posted["type"], posted["textFormat"], posted["replyToId"]) == ("message", "xml", REPLY_ID)
    assert posted["text"] == (
        f"Fixed &lt;script&gt;x&lt;/script&gt; &amp; &quot;done&quot;<br>Bye<br><br>Ref: {reference('t1')}"
    )
    assert posted["id"] == reference("t1")


async def test_a_teams_reply_is_sent_once_per_action_even_after_a_restart(mcp: Mcp, fake: FakeMicrosoft) -> None:
    arguments = {"key": ROOT, "text": "We are on it.", "action_key": "t1", "work_item_key": ROOT}
    assert (await mcp.call("reply", **arguments))["sent"] is True
    assert (await mcp.call("reply", **arguments))["sent"] is False  # this process remembers it
    assert (await restarted(fake).call("reply", **arguments))["sent"] is False  # Graph shows it in the thread
    assert len(fake.connector_replies) == 1


async def test_a_connector_conflict_is_a_reply_already_made(mcp: Mcp, fake: FakeMicrosoft) -> None:
    fake.graph_lag = True  # the reply is not yet visible in Graph
    fake.reject_duplicate_ids = True
    arguments = {"key": ROOT, "text": "We are on it.", "action_key": "t1", "work_item_key": ROOT}
    assert (await mcp.call("reply", **arguments))["sent"] is True
    assert (await restarted(fake).call("reply", **arguments))["sent"] is False
    assert len(fake.connector_replies) == 1


async def test_the_residual_window_a_restart_while_graph_lags(mcp: Mcp, fake: FakeMicrosoft) -> None:
    """Said in the README: a new process, before Graph shows the first reply, against a connector that keeps
    no activity id, sends the reply twice."""
    fake.graph_lag = True
    arguments = {"key": ROOT, "text": "We are on it.", "action_key": "t1", "work_item_key": ROOT}
    await mcp.call("reply", **arguments)
    await restarted(fake).call("reply", **arguments)
    assert len(fake.connector_replies) == 2


async def test_the_reply_goes_to_the_service_url_of_the_channels_last_verified_activity(
    mcp: Mcp, fake: FakeMicrosoft
) -> None:
    emea = "https://smba.trafficmanager.net/emea/"
    body = activity(service_url=emea)
    token = fake.issuer.mint(service_url=emea)
    assert (await mcp.call("tracker_parse_event", headers={"Authorization": f"Bearer {token}"}, body=json.dumps(body)))[
        "events"
    ]
    await mcp.call("reply", key=ROOT, work_item_key=ROOT, text="Hi.", action_key="t1")
    assert fake.connector_replies[0]["region"] == "emea"


async def test_a_service_url_outside_the_bot_connector_is_never_used(mcp: Mcp, fake: FakeMicrosoft) -> None:
    evil = "https://smba.example.test/teams/"
    body = activity(service_url=evil)
    token = fake.issuer.mint(service_url=evil)
    answer = await mcp.call("tracker_parse_event", headers={"Authorization": f"Bearer {token}"}, body=json.dumps(body))
    assert answer["verified"] is True
    await mcp.call("reply", key=ROOT, work_item_key=ROOT, text="Hi.", action_key="t1")
    assert fake.connector_replies[0]["region"] == "teams"
    assert not [r for r in fake.requests if r.url.host == "smba.example.test"]


async def test_the_platforms_comment_never_reaches_the_requester(mcp: Mcp, fake: FakeMicrosoft) -> None:
    """The platform's own comments (a question with a console link, a command's answer) are not the
    requester's to read: only `reply`, a governed action's effect, writes in their thread."""
    comment = await mcp.call("tracker_comment", key=ROOT, markdown="See https://console/approvals", action_key="p:x")
    assert comment["written"] is False
    assert fake.connector_replies == [] and fake.sent_mail == []


async def test_a_tenant_refusing_a_rich_create_reply_gets_the_documented_sequence(
    mcp: Mcp, fake: FakeMicrosoft
) -> None:
    """Graph documents createReply's `message` for its body only: refused (400), the server makes a bare
    draft, updates its recipients, body and mark, then sends it — to the sender only, once per action."""
    fake.strict = True
    fake.add_message("AAkAreplyto", "Help", "kim.lee@example.test", "b", reply_to=["attacker@evil.test"])
    key = "mail:AAkAreplyto"
    first = await mcp.call("reply", key=key, work_item_key=key, text="Done.", action_key="a1")
    again = await mcp.call("reply", key=key, work_item_key=key, text="Done.", action_key="a1")
    assert (first["sent"], again["sent"]) == (True, False)
    assert [m["to"] for m in fake.sent_mail] == [["kim.lee@example.test"]]
    [sent] = fake.folder("sentitems")
    assert (sent.properties[ACTION_PROPERTY], sent.body) == ("a1", "Done.")
