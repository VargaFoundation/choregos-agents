# Microsoft 365 desk plugin

An IT desk's requests as Tranibor work items, from two Microsoft 365 intake channels, and the requester
answered in the original thread:

1. **A shared mailbox** (`it-desk@contoso.com`), read and answered with Microsoft Graph application
   permissions that Exchange **RBAC for Applications** restricts to that one mailbox. Polled; no Graph
   subscription.
2. **Teams channels through a bot**: a message that mentions the bot reaches the platform's webhook as a
   Bot Connector activity; the server verifies its JWT, reads the message with Graph (the Teams app's
   resource-specific consent `ChannelMessage.Read.Group`) and replies in the thread through the Bot
   Connector.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `microsoft-365-desk` 0.1.0, core `>=0.28.0,<1.0.0` (the first core with `webhook.forward_authorization`, ADR 0198) |
| Server | [`servers/desk.yaml`](servers/desk.yaml), [`server/`](server) | the MCP server `desk`: a plugin tracker (`implements: [tracker]`), its `reply`, and its `fake` |
| Tracker profile | [`server/TRACKER_PROFILE.md`](server/TRACKER_PROFILE.md) | keys, every tool's mapping, idempotence |
| Procedure | [`procedures/connector.mcp.microsoft-365-desk.md`](procedures/connector.mcp.microsoft-365-desk.md) | the Entra application, the mailbox's RBAC scope, the Azure Bot, the Teams app |
| Teams app | [`teams/manifest.json`](teams/manifest.json), [`teams/build_app.py`](teams/build_app.py) | a Teams app manifest (v1.16) template — the bot and the RSC permission — and the script that fills it and zips it with its two icons (`server/tests/test_teams_app.py`). Not part of the packed plugin (only what `contents` declares is) |
| End to end | [`e2e/test_m365_desk_on_the_core_bench.py`](e2e/test_m365_desk_on_the_core_bench.py) | the `access-requests-m365` template on this server, on the core's e2e bench: a mail granted by the CRM's approvers and answered in its thread, a Teams mention answered by a person of the desk (CI's `e2e` job) |

```sh
tranibor plugin validate plugins/microsoft-365-desk
tranibor plugin pack plugins/microsoft-365-desk -o microsoft-365-desk-0.1.0.tar.gz   # same bytes every time
```

`validate` reads the whole directory: run it on a clean tree (no `server/.venv` nor caches), as CI does.

## The tools

| Tool | Access | What it does |
|:--|:--|:--|
| `tracker_fetch_item` | read | a request by key: subject, plain-text body (≤ 8000 characters), requester, link, state. Mail: `mail:<immutable id>`; Teams: `teams:<conversationId>;<activityId>` |
| `tracker_list_candidates` | read | the Inbox's messages no one took (no `Choregos:` category), newest first, without automatic messages (auto-replies, out-of-office, delivery reports) nor follow-ups of a thread already taken. Teams: none, events only |
| `tracker_parse_event` | read | a Bot Connector delivery: its JWT verified, a message that mentions the bot in a configured channel becomes `tracker.item.created`; any other message of the channel gives nothing |
| `tracker_create_item` | write | refused: requests come from the mailbox and the channels |
| `tracker_set_state` | write | mail: the category `Choregos: <state>` (the status, else the state the label names); Teams: nothing |
| `tracker_upsert_status` | write | mail: Tranibor's status in an extended property of the message, never seen by the requester; Teams: nothing |
| `tracker_close` | write | mail: `Choregos: Done`; nothing sent; Teams: nothing |
| `tracker_comment` | write, `platform_arguments: [action_key]` | nothing: a successful no-op (below) |
| `reply` | write, `platform_arguments: [action_key, work_item_key]` | the agent's answer to the requester (a mail to the sender only, or a Teams reply in the thread), plain text ≤ 4000 characters, only in the thread of the action's own work item or of its fan-out parent; refused without a work item. No `human_approval` floor: a write waits for approval by default, and the organisation's policy decides |

A key that is not this desk's (a fan-out child's `mail:<id>.<n>`, another tracker's) is refused by a read
and by `reply`, and is a successful no-op for every write the platform mirrors, so a child's state never
fails on the parent's tracker. A child's action may still `reply`: its `work_item_key` (`<key>.<n>`) names
its parent's thread.

**Only messages the platform took are categorised.** The core mirrors every state with a label
(`choregos:<state>` when the workflow maps none) and a status when it maps one; `tracker_set_state`
writes the category from either, so a taken message leaves the candidates, and an untaken one stays.

**`tracker_comment` writes nothing.** The core's adapter calls it for the platform's own comments on
the tracker (a human request's question with a console link, the answer to a `/choregos` command…).
Those are for the platform's people, not the requester: on this tracker they are a successful no-op.
Only `reply`, a governed action's effect whose text a workflow decides, writes in the requester's
thread.

## Idempotence

- **A mail reply** carries the action key in a single-value extended property. Before sending, Sent Items
  is searched for it (found: nothing is sent), then Drafts (a draft an interrupted attempt left is sent,
  not duplicated). Residual window: a replay between `send` and the moment Sent Items shows the message.
- **A Teams reply** carries a visible reference, `Ref: desk-<12 hex>`, derived from the action key, which is
  also the outgoing activity's `id`. It is not sent again when this process already sent it, when Graph
  shows a reply of the bot carrying the reference in the thread, or when the Bot Connector answers `409`
  (Teams is not known to keep client ids: a last resort). Residual window: a new process (a restart, a
  second replica) replaying the action before Graph shows the first reply sends it twice — a test says so
  (`test_reply.py::test_the_residual_window_a_restart_while_graph_lags`).
- **State writes** are idempotent PATCHes (`changed`/`written`/`closed: false` when nothing changes); with
  nothing to write they call nobody, and on a message that is gone they are a successful no-op.
- **A mail reply's draft is born marked**: one `createReply` carries the recipient (the sender, never the
  Reply-To, no Cc nor Bcc), the body and the action's property. Graph documents that call's `message` for
  its body only: a tenant that refuses the rest (`400`) gets the documented sequence — a bare
  `createReply`, an update of the draft, `send` — whose cut between the first two leaves an unmarked
  draft that is never sent.

## Choices

- **Keys.** The profile bounds a key to 128 characters. Mail keys use Graph **immutable** ids (every call
  sends `Prefer: IdType="ImmutableId"`; ~70 characters, unchanged when a message moves); an id longer than
  119 characters is never made into a key, so that a fan-out child's `mail:<id>.<n>` (n up to 99) fits too,
  and `tracker_list_candidates` says how many it skipped. A Teams key holds the channel, the thread's root
  and the message (~90 characters, at most 125): enough to read it and to reply in its thread, with no
  state of the server's own.
- **Teams deliveries.** `channelData.team.id` is the team's thread id (`19:…@thread.tacv2`), not its group
  id: the configured channel (also the conversation's) and the tenant decide, and `team.aadGroupId`, when
  sent, must be the configured `team_id`. Every channel message is delivered (resource-specific consent):
  only one that mentions the bot is a request. The Bot Framework's keys are read one at a time, at most
  every 5 minutes for an unknown key id, 30 seconds after a failed read.
- **The Bot Connector's service URL** is not in the key (it would not fit). The server keeps the service
  URL of each channel's last verified activity in memory, and uses `service_url` (default
  `https://smba.trafficmanager.net/teams/`) when it has none (after a restart). A service URL outside
  `smba.trafficmanager.net`, the declared egress, is never used: the bot's token goes nowhere else.
- **Text.** HTML is reduced to plain text (no tag, style, script, image: nothing remote is fetched). A
  mail reply is sent as `text`; a Teams reply as `xml` with every character of the input escaped, so
  nothing of it is markup.
- **`POST /events`** answers the same as `tracker_parse_event`, its body `{headers, body}`, behind the
  platform's token: the platform itself calls the tool over `/mcp`, with the delivery's `Authorization`
  forwarded (core ADR 0198).

## What it does not do

- **Attachments**: never read (an item says `has_attachments`); a person opens them.
- **Teams one-to-one and group chats**: only the configured channels (`team` scope); a chat with the bot
  gives no work item.
- **Graph change notifications** (subscriptions): the mailbox is polled.
- **Prompt-injection detection**: every subject, body and message is third-party data the agent must read
  as data; the server does not judge it.
- **State on Teams**: Teams keeps none of Tranibor's; state writes on a Teams item do nothing.
- **Sovereign clouds**: Graph, Entra and the Bot Connector of the commercial cloud only (fixed hosts).

## The server

`server/` is a Python 3.12 package (`choregos_m365_desk`): MCP over streamable HTTP (stateless, JSON
responses) at `/mcp` behind the platform's bearer token (`CHOREGOS_SERVER_TOKEN`), `/healthz` open.
`M365_DESK_FAKE=1` runs it on a seeded in-memory tenant (`fake.py`): Entra, Graph (the shared mailbox
with three messages — plain, HTML, already taken — and a Teams thread), the Bot Framework's keys (a test
issuer with its own RSA key) and the Bot Connector, which records replies. The platform runs the fake with
that variable and its token only (`fake` in `servers/desk.yaml`).

```sh
cd plugins/microsoft-365-desk/server
uv sync --all-extras --dev
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && uv run pytest -q
```

The tests check every tracker answer against the core's normative profile
(`packages/contracts/schemas/plugin-tracker-profile.schema.json`) when a core checkout is found
(`TRANIBOR_CORE`, else `../tranibor` beside this repository); without it they still run, and
`test_the_core_profile_is_there` is skipped with the reason.
