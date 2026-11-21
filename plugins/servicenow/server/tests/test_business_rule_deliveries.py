# SPDX-License-Identifier: Apache-2.0
"""The procedure's Business Rule, as a real instance posted it.

The four bodies below are what the rule of `procedures/connector.mcp.servicenow.md` (step 5, unchanged)
sent from a Personal Developer Instance on the Australia release, 2026-11-21, for one incident of the
`Service Desk` group: filed, its urgency changed, a customer comment, then resolved. They were read from
the instance's outbound HTTP log (`sys_outbound_http_log`, log level `all`), since a developer machine has
no public URL for the instance to reach; the headers are the ones the log recorded, with the secret
replaced. What this proves: the server reads what the rule really sends. What it does not: a delivery
through a real ingress to the platform's webhook route.
"""

from __future__ import annotations

from choregos_servicenow.config import FAKE_WEBHOOK_SECRET

from .conftest import Mcp

SYS_ID = "88f5ec88c33413107e71becdd40131e5"
BODIES = [
    '{"table":"incident","sys_id":"88f5ec88c33413107e71becdd40131e5","number":"INC0010006","sys_mod_count":0,"operation":"insert","state":"1"}',
    '{"table":"incident","sys_id":"88f5ec88c33413107e71becdd40131e5","number":"INC0010006","sys_mod_count":1,"operation":"update","state":"1"}',
    '{"table":"incident","sys_id":"88f5ec88c33413107e71becdd40131e5","number":"INC0010006","sys_mod_count":2,"operation":"comment","state":"1"}',
    '{"table":"incident","sys_id":"88f5ec88c33413107e71becdd40131e5","number":"INC0010006","sys_mod_count":3,"operation":"update","state":"6"}',
]  # fmt: skip
#: As the log recorded them (`X-SNC-INTEGRATION-SOURCE` is the instance's own, the rule does not set it).
HEADERS = {
    "Content-Type": "application/json",
    "X-Choregos-Secret": FAKE_WEBHOOK_SECRET,
    "X-SNC-INTEGRATION-SOURCE": "7f6d3fbd1b7e79506a85eb9b2d4bcb6f",
    "User-Agent": "ServiceNow/1.0",
}


async def test_each_delivery_of_a_real_business_rule_is_one_event(mcp: Mcp) -> None:
    kinds = []
    for body in BODIES:
        parsed = await mcp.call("tracker_parse_event", headers=HEADERS, body=body)
        assert parsed["verified"] is True
        (event,) = parsed["events"]
        assert event["item_key"] == "INC0010006"
        kinds.append((event["kind"], event["delivery_id"]))
    assert kinds == [
        ("tracker.item.created", f"{SYS_ID}:0"),
        ("tracker.item.updated", f"{SYS_ID}:1"),
        ("tracker.item.commented", f"{SYS_ID}:2"),
        ("tracker.item.closed", f"{SYS_ID}:3"),
    ]


async def test_a_real_delivery_with_another_secret_is_not_verified(mcp: Mcp) -> None:
    parsed = await mcp.call("tracker_parse_event", headers={**HEADERS, "X-Choregos-Secret": "x" * 48}, body=BODIES[0])
    assert parsed == {"verified": False, "events": []}
