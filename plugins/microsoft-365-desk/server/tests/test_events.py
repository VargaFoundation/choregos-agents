# SPDX-License-Identifier: Apache-2.0
"""`tracker_parse_event`: a Bot Connector delivery is verified (its JWT) before anything of it is used."""

from __future__ import annotations

import json
from typing import Any

import pytest

from choregos_m365_desk.botauth import KEYS_REFRESH_MIN_S, KEYS_RETRY_AFTER_FAILURE_S, BotAuth
from choregos_m365_desk.config import FAKE_CLIENT_ID
from choregos_m365_desk.fake import CONVERSATION_ID, REPLY_ID, ROOT_ID, FakeBotIssuer, FakeMicrosoft, activity

from .conftest import Mcp, Tracker

ROOT = f"teams:{CONVERSATION_ID};{ROOT_ID}"
UNVERIFIED = {"verified": False, "events": []}


def delivery(token: str | None, body: dict[str, Any], header: str = "Authorization") -> dict[str, Any]:
    headers = {"Content-Type": "application/json", **({header: f"Bearer {token}"} if token else {})}
    return {"headers": headers, "body": json.dumps(body)}


async def test_a_mention_in_a_configured_channel_is_a_new_work_item(tracker: Tracker, fake: FakeMicrosoft) -> None:
    answer = await tracker("tracker_parse_event", **delivery(fake.issuer.mint(), activity()))
    assert answer == {
        "verified": True,
        "events": [{"kind": "tracker.item.created", "item_key": ROOT, "delivery_id": ROOT_ID}],
    }
    # The key it gives is one the desk reads back.
    assert (await tracker("tracker_fetch_item", key=ROOT))["key"] == ROOT


async def test_a_mention_in_a_reply_names_the_reply_in_its_thread(tracker: Tracker, fake: FakeMicrosoft) -> None:
    body = activity("<at>Desk</at> Same for me on floor 2.", activity_id=REPLY_ID, root_id=ROOT_ID)
    answer = await tracker("tracker_parse_event", **delivery(fake.issuer.mint(), body))
    key = f"teams:{CONVERSATION_ID};{REPLY_ID}"
    assert answer["events"] == [{"kind": "tracker.item.created", "item_key": key, "delivery_id": REPLY_ID}]


@pytest.mark.parametrize("header", ["authorization", "AUTHORIZATION", "Authorization"])
async def test_the_header_is_found_whatever_its_case(mcp: Mcp, fake: FakeMicrosoft, header: str) -> None:
    assert (await mcp.call("tracker_parse_event", **delivery(fake.issuer.mint(), activity(), header)))["verified"]


async def test_no_authorization_header_is_simply_not_verified(tracker: Tracker) -> None:
    assert await tracker("tracker_parse_event", **delivery(None, activity())) == UNVERIFIED


@pytest.mark.parametrize(
    "mint",
    [
        pytest.param({"expires_in": -600}, id="expired beyond the 5 minutes of skew"),
        pytest.param({"audience": "99999999-9999-4999-8999-999999999999"}, id="another bot's audience"),
        pytest.param({"issuer": "https://sts.windows.net/x/"}, id="another issuer"),
        pytest.param({"service_url": "https://smba.trafficmanager.net/emea/"}, id="serviceurl not the activity's"),
    ],
)
async def test_a_token_that_does_not_hold_is_not_verified(
    tracker: Tracker, fake: FakeMicrosoft, mint: dict[str, Any]
) -> None:
    assert await tracker("tracker_parse_event", **delivery(fake.issuer.mint(**mint), activity())) == UNVERIFIED


async def test_a_token_within_the_clock_skew_is_verified(mcp: Mcp, fake: FakeMicrosoft) -> None:
    assert (await mcp.call("tracker_parse_event", **delivery(fake.issuer.mint(expires_in=-120), activity())))[
        "verified"
    ]


async def test_a_token_without_a_serviceurl_claim_is_verified(mcp: Mcp, fake: FakeMicrosoft) -> None:
    assert (await mcp.call("tracker_parse_event", **delivery(fake.issuer.mint(service_url=None), activity())))[
        "verified"
    ]


async def test_a_key_the_bot_framework_does_not_publish_is_not_verified(mcp: Mcp, fake: FakeMicrosoft) -> None:
    stranger = FakeBotIssuer(kid="not-published")
    assert await mcp.call("tracker_parse_event", **delivery(stranger.mint(), activity())) == UNVERIFIED
    impostor = FakeBotIssuer(kid=fake.issuer.kid)  # the published kid, another key
    assert await mcp.call("tracker_parse_event", **delivery(impostor.mint(), activity())) == UNVERIFIED


async def test_a_key_not_endorsed_for_teams_is_not_verified() -> None:
    fake = FakeMicrosoft(FakeBotIssuer(endorsements=("webchat",)))
    from choregos_m365_desk.app import Runtime, build_server
    from choregos_m365_desk.config import Settings

    mcp = Mcp(build_server(Runtime(Settings.from_env({"M365_DESK_FAKE": "1"}), fake=fake)))
    assert await mcp.call("tracker_parse_event", **delivery(fake.issuer.mint(), activity())) == UNVERIFIED


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c", ""])
async def test_a_malformed_token_is_not_verified(mcp: Mcp, token: str) -> None:
    raw = {"headers": {"Authorization": f"Bearer {token}"}, "body": json.dumps(activity())}
    assert await mcp.call("tracker_parse_event", **raw) == UNVERIFIED


async def test_another_algorithm_is_not_verified(mcp: Mcp, fake: FakeMicrosoft) -> None:
    import jwt

    forged = jwt.encode({"iss": "https://api.botframework.com", "aud": FAKE_CLIENT_ID}, "k" * 32, algorithm="HS256")
    assert await mcp.call("tracker_parse_event", **delivery(forged, activity())) == UNVERIFIED
    assert await mcp.call("tracker_parse_event", **delivery("x", activity(), "Authorization")) == UNVERIFIED
    basic = {"headers": {"Authorization": "Basic dTpw"}, "body": json.dumps(activity())}
    assert await mcp.call("tracker_parse_event", **basic) == UNVERIFIED


@pytest.mark.parametrize("body", ["not json", "[]", json.dumps("x"), "{" * 10])
async def test_a_malformed_body_is_not_verified(mcp: Mcp, fake: FakeMicrosoft, body: str) -> None:
    raw = {"headers": {"Authorization": f"Bearer {fake.issuer.mint()}"}, "body": body}
    assert await mcp.call("tracker_parse_event", **raw) == UNVERIFIED


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(activity(kind="conversationUpdate"), id="not a message"),
        pytest.param(activity(kind="messageReaction"), id="a reaction"),
        pytest.param(activity(channel_id="19:otherchannel@thread.tacv2"), id="another channel"),
        pytest.param(activity(aad_group_id="33333333-3333-4333-8333-333333333333"), id="another team's group id"),
        pytest.param(activity("My printer is broken.", mention=False), id="no mention of the bot"),
        pytest.param(activity(tenant_id="44444444-4444-4444-8444-444444444444"), id="another tenant"),
        pytest.param(activity("<at>Desk</at>   "), id="a mention and nothing else"),
        pytest.param(activity(activity_id="not-digits"), id="an activity id that is not Teams'"),
    ],
)
async def test_a_verified_delivery_that_is_not_a_request_gives_no_event(
    tracker: Tracker, fake: FakeMicrosoft, body: dict[str, Any]
) -> None:
    assert await tracker("tracker_parse_event", **delivery(fake.issuer.mint(), body)) == {
        "verified": True,
        "events": [],
    }


async def test_a_real_teams_activity_names_its_team_by_thread_id_and_maybe_by_group_id(
    mcp: Mcp, fake: FakeMicrosoft
) -> None:
    """`channelData.team.id` is the team's thread id (`19:…@thread.tacv2`), never its group id; the group id
    comes as `team.aadGroupId`, sometimes. The configured channel (checked against the conversation) and
    the tenant decide; a group id, when sent, must be the configured team's."""
    with_group = activity()
    assert with_group["channelData"]["team"]["id"].startswith("19:")
    for body in (with_group, activity(aad_group_id=None)):
        answer = await mcp.call("tracker_parse_event", **delivery(fake.issuer.mint(), body))
        assert [e["item_key"] for e in answer["events"]] == [ROOT]


async def test_a_mention_of_someone_else_is_not_a_request(mcp: Mcp, fake: FakeMicrosoft) -> None:
    """With the channel's RSC every message is delivered: only one that mentions THIS bot is a request."""
    body = activity("<at>Alex</at> can you look?", mention=False)
    body["entities"].append(
        {"type": "mention", "text": "<at>Alex</at>", "mentioned": {"id": "29:alex", "name": "Alex"}}
    )
    assert (await mcp.call("tracker_parse_event", **delivery(fake.issuer.mint(), body)))["events"] == []
    other_bot = activity(bot_id="99999999-9999-4999-8999-999999999999")
    other_bot["recipient"]["id"] = f"28:{FAKE_CLIENT_ID}"
    assert (await mcp.call("tracker_parse_event", **delivery(fake.issuer.mint(), other_bot)))["events"] == []


async def test_a_conversation_of_another_channel_than_its_channel_data_gives_no_event(
    mcp: Mcp, fake: FakeMicrosoft
) -> None:
    body = activity()
    body["conversation"]["id"] = f"19:otherchannel@thread.tacv2;messageid={ROOT_ID}"
    assert (await mcp.call("tracker_parse_event", **delivery(fake.issuer.mint(), body)))["events"] == []


async def test_keys_are_read_once_and_kept(mcp: Mcp, fake: FakeMicrosoft) -> None:
    for _ in range(3):
        await mcp.call("tracker_parse_event", **delivery(fake.issuer.mint(), activity()))
    stranger = FakeBotIssuer(kid="not-published")
    for _ in range(3):
        await mcp.call("tracker_parse_event", **delivery(stranger.mint(), activity()))
    reads = [r for r in fake.requests if r.url.host == "login.botframework.com"]
    # The metadata and the keys, once: an unknown kid does not re-read them within 5 minutes.
    assert [r.url.path for r in reads] == ["/v1/.well-known/openidconfiguration", "/v1/.well-known/keys"]


async def test_an_unknown_kid_re_reads_the_keys_after_five_minutes() -> None:
    import httpx

    fake = FakeMicrosoft()
    now = [1000.0]
    auth = BotAuth(httpx.AsyncClient(transport=httpx.MockTransport(fake)), FAKE_CLIENT_ID, clock=lambda: now[0])
    assert await auth.verify(f"Bearer {fake.issuer.mint()}", activity()) is not None
    rotated = FakeBotIssuer(kid="rotated")
    fake.issuer = rotated
    assert await auth.verify(f"Bearer {rotated.mint()}", activity()) is None  # within 5 minutes: not re-read
    now[0] += KEYS_REFRESH_MIN_S + 1
    assert await auth.verify(f"Bearer {rotated.mint()}", activity()) is not None


async def test_keys_that_cannot_be_read_are_not_re_read_at_every_delivery() -> None:
    import httpx

    fake = FakeMicrosoft()
    fake.bot_keys_down = True
    now = [1000.0]
    auth = BotAuth(httpx.AsyncClient(transport=httpx.MockTransport(fake)), FAKE_CLIENT_ID, clock=lambda: now[0])

    def reads() -> int:
        return len([r for r in fake.requests if r.url.path.endswith("openidconfiguration")])

    for _ in range(5):
        assert await auth.verify(f"Bearer {fake.issuer.mint()}", activity()) is None
    assert reads() == 1
    fake.bot_keys_down = False
    now[0] += KEYS_RETRY_AFTER_FAILURE_S - 1
    assert await auth.verify(f"Bearer {fake.issuer.mint()}", activity()) is None
    assert reads() == 1
    now[0] += 2  # a shorter back-off after a failure than after a read
    assert await auth.verify(f"Bearer {fake.issuer.mint()}", activity()) is not None
    assert reads() == 2


async def test_concurrent_deliveries_with_forged_kids_read_the_keys_once() -> None:
    import asyncio

    import httpx

    fake = FakeMicrosoft()
    inner = httpx.MockTransport(fake)

    class Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.01)
            return await inner.handle_async_request(request)

    auth = BotAuth(httpx.AsyncClient(transport=Slow()), FAKE_CLIENT_ID)
    forged = [FakeBotIssuer(kid=f"forged-{n}").mint() for n in range(5)]
    results = await asyncio.gather(*(auth.verify(f"Bearer {token}", activity()) for token in forged))
    assert results == [None] * 5
    assert len([r for r in fake.requests if r.url.path.endswith("openidconfiguration")]) == 1


async def test_the_platforms_door_events_answers_as_the_tool() -> None:
    from starlette.testclient import TestClient

    from choregos_m365_desk.app import build_app
    from choregos_m365_desk.config import Settings

    fake = FakeMicrosoft()
    settings = Settings.from_env({"M365_DESK_FAKE": "1", "CHOREGOS_SERVER_TOKEN": "t"})
    with TestClient(build_app(settings, fake=fake), headers={"Authorization": "Bearer t"}) as client:
        good = client.post("/events", json=delivery(fake.issuer.mint(), activity()))
        assert (good.status_code, good.json()["events"][0]["item_key"]) == (200, ROOT)
        assert client.post("/events", json=delivery(None, activity())).status_code == 401
        assert client.post("/events", content=b"nope").status_code == 400
        assert client.post("/events", json={"headers": {}, "body": 3}).status_code == 400
        assert (
            client.post(
                "/events", json=delivery(fake.issuer.mint(), activity()), headers={"Authorization": ""}
            ).status_code
            == 401
        )
