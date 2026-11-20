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
    await mcp.call("reply", key=PLAIN, text="One.", action_key="a1")
    await mcp.call("reply", key=PLAIN, text="Two.", action_key="a2")
    assert [m["body"] for m in fake.sent_mail] == ["One.", "Two."]


async def test_a_draft_left_by_an_interrupted_attempt_is_sent_not_duplicated(mcp: Mcp, fake: FakeMicrosoft) -> None:
    fake.add_message(message_id(900), "RE: VPN token expired", "it-desk@example.test", "Draft.", folder="drafts")
    fake.messages[message_id(900)].properties[ACTION_PROPERTY] = "a1"
    fake.messages[message_id(900)].to = ["alex.durand@example.test"]
    assert (await mcp.call("reply", key=PLAIN, text="Draft.", action_key="a1"))["sent"] is True
    assert [m["id"] for m in fake.sent_mail] == [message_id(900)]
    assert not [r for r in fake.requests if r.url.path.endswith("/createReply")]


async def test_the_mail_body_is_plain_text_never_markup(mcp: Mcp, fake: FakeMicrosoft) -> None:
    text = "<b>bold?</b> <img src=https://x.test/p.gif>"
    await mcp.call("reply", key=PLAIN, text=text, action_key="a'quoted")
    patch = next(r for r in fake.requests if r.method == "PATCH")
    assert json.loads(patch.content)["body"] == {"contentType": "text", "content": text}
    assert fake.sent_mail[0]["body"] == text


async def test_a_reply_to_a_mail_that_is_gone_is_refused(mcp: Mcp, fake: FakeMicrosoft) -> None:
    assert "refused: no message" in await mcp.error("reply", key="mail:AAkAgone", text="Hi.", action_key="a1")
    assert fake.sent_mail == []


# ─────────────────────────── what any reply must hold ───────────────────────────


@pytest.mark.parametrize(
    ("arguments", "why"),
    [
        ({"key": PLAIN, "text": "   "}, "needs a text"),
        ({"key": PLAIN, "text": "x" * 4001}, "at most 4000"),
        ({"key": "DESK-1.1", "text": "Hi."}, "not a work item of this desk"),
        ({"key": PLAIN, "text": "Hi.", "work_item_key": f"mail:{message_id(2)}"}, "own work item"),
        ({"key": ROOT, "text": "Hi.", "work_item_key": PLAIN}, "own work item"),
        ({"key": "teams:19:0000@thread.tacv2;messageid=1;1", "text": "Hi."}, "not one this desk answers"),
    ],
)
async def test_a_reply_that_breaks_a_rule_is_refused_before_anything_is_sent(
    mcp: Mcp, fake: FakeMicrosoft, arguments: dict[str, str], why: str
) -> None:
    error = await mcp.error("reply", action_key="a1", **arguments)
    assert "refused:" in error and why in error, error
    assert fake.sent_mail == [] and fake.connector_replies == []


async def test_a_work_item_of_another_tracker_does_not_bind_the_thread(mcp: Mcp, fake: FakeMicrosoft) -> None:
    """A child work item of a fan-out (`DESK-1.1`) answers its parent's requester."""
    assert (await mcp.call("reply", key=PLAIN, text="Hi.", action_key="a1", work_item_key="DESK-1.1"))["sent"]


# ─────────────────────────── Teams ───────────────────────────


async def test_a_teams_reply_goes_to_the_thread_escaped(mcp: Mcp, fake: FakeMicrosoft) -> None:
    sent = await mcp.call("reply", key=REPLY, text='Fixed <script>x</script> & "done"\nBye', action_key="t1")
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
    arguments = {"key": ROOT, "text": "We are on it.", "action_key": "t1"}
    assert (await mcp.call("reply", **arguments))["sent"] is True
    assert (await restarted(fake).call("reply", **arguments))["sent"] is False
    assert len(fake.connector_replies) == 1


async def test_the_residual_window_a_restart_while_graph_lags(mcp: Mcp, fake: FakeMicrosoft) -> None:
    """Said in the README: a new process, before Graph shows the first reply, against a connector that keeps
    no activity id, sends the reply twice."""
    fake.graph_lag = True
    arguments = {"key": ROOT, "text": "We are on it.", "action_key": "t1"}
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
    await mcp.call("reply", key=ROOT, text="Hi.", action_key="t1")
    assert fake.connector_replies[0]["region"] == "emea"


async def test_a_service_url_outside_the_bot_connector_is_never_used(mcp: Mcp, fake: FakeMicrosoft) -> None:
    evil = "https://smba.example.test/teams/"
    body = activity(service_url=evil)
    token = fake.issuer.mint(service_url=evil)
    answer = await mcp.call("tracker_parse_event", headers={"Authorization": f"Bearer {token}"}, body=json.dumps(body))
    assert answer["verified"] is True
    await mcp.call("reply", key=ROOT, text="Hi.", action_key="t1")
    assert fake.connector_replies[0]["region"] == "teams"
    assert not [r for r in fake.requests if r.url.host == "smba.example.test"]


async def test_the_platforms_comment_never_reaches_the_requester(mcp: Mcp, fake: FakeMicrosoft) -> None:
    """The platform's own comments (a question with a console link, a command's answer) are not the
    requester's to read: only `reply`, a governed action's effect, writes in their thread."""
    comment = await mcp.call("tracker_comment", key=ROOT, markdown="See https://console/approvals", action_key="p:x")
    assert comment["written"] is False
    assert fake.connector_replies == [] and fake.sent_mail == []
