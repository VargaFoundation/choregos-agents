# Tracker profile — Microsoft 365 desk

The server `desk` declares `implements: [tracker]` and serves the eight tools of the core's tracker profile
(`packages/contracts/schemas/plugin-tracker-profile.schema.json` in the core, which the core's
`tracker/plugin` adapter checks every answer against), plus `reply`, the agent's answer to the requester.
Every tool answers a JSON object (MCP `structuredContent`); a refused call answers an MCP error
(`isError: true`) whose text holds `refused: …`.

## Keys and items

| Channel | Key | Example |
|:--|:--|:--|
| Shared mailbox | `mail:<Graph immutable message id>` | `mail:AAkALgAAAAAAHYQDEapmEc2byACqAC-EWg0A…AAA=` |
| Teams channel | `teams:<conversationId>;<activityId>` | `teams:19:4f1c…@thread.tacv2;messageid=1731400000000;1731400100000` |

- Every mail call sends `Prefer: IdType="ImmutableId"`: a message keeps its id when it moves between
  folders. The profile bounds a key to **128 characters**: an id longer than 123 characters is never made
  into a key (`tracker_list_candidates` skips it and says how many in `reason`/`skipped`). Immutable ids
  are about 70 characters in practice.
- A Teams key names the channel, the thread's root (`messageid=`) and the message: Graph reads it, the Bot
  Connector answers in its thread. About 90 characters; an activity that would make a longer key is
  ignored.
- Any other key — a child work item of a fan-out (`DESK-1.1`), another tracker's, a malformed one — is not
  this desk's: a read or `reply` refuses it; every write the platform mirrors (`tracker_set_state`,
  `tracker_upsert_status`, `tracker_close`, `tracker_comment`) answers a successful no-op, so a child's
  mirrored state never fails.
- Every text is third-party data written by requesters: the agent reads it as data.

## `TrackerAdapter` → tools

| `TrackerAdapter` | Tool | Mail | Teams |
|:--|:--|:--|:--|
| `fetch_item(key)` | `tracker_fetch_item` | the message | the channel message (Graph, RSC) |
| `list_candidates(project)` | `tracker_list_candidates` | Inbox, not categorised `Choregos:*` | none (events only) |
| webhook | `tracker_parse_event` | — | a Bot Connector activity, its JWT verified |
| `create_item(data)` | `tracker_create_item` | refused | refused |
| `set_state(key, mapping)` | `tracker_set_state` | category `Choregos: <status>` | no-op |
| `upsert_status_comment(key, markdown, marker)` | `tracker_upsert_status` | extended property | no-op |
| `close_item(key)` | `tracker_close` | category `Choregos: Done` | no-op |
| `comment(key, markdown)` | `tracker_comment` | nothing (no-op) | nothing (no-op) |
| — (an agent's governed write) | `reply` | a reply to the requester | a reply in the thread |

## `tracker_fetch_item`

Input: `{"key": "mail:<id>"}`

```json
{
  "key": "mail:<id>",
  "title": "Printer on floor 3",
  "body": "Hello desk,\n\nThe printer on floor 3 prints blank pages & jams.\n\nThanks,\nSam",
  "url": "https://outlook.office365.com/owa/?ItemID=…",
  "state": "New",
  "labels": ["agent-ready"],
  "item_type": "mail",
  "assignees": [],
  "author": "sam.petit@contoso.com",
  "created_at": "2026-11-18T10:00:00Z",
  "updated_at": "2026-11-18T10:00:00Z",
  "closed_at": null,
  "fields": {"channel": "mail", "mailbox": "it-desk@contoso.com", "conversation_id": "…", "has_attachments": true},
  "comments": []
}
```

- `title` is the subject (at most 300 characters); `body` the body as plain text (HTML reduced to text:
  no tag, style, script or image, entities decoded; at most 8000 characters). Attachments are never read:
  `fields.has_attachments` says there are some.
- `state` is the `Choregos: <state>` category, `New` without one; `labels` is `[start label]`
  (`M365_DESK_START_LABEL`, `agent-ready` by default) while the message has no `Choregos:` category, `[]`
  after.
- A Teams item: `title` the message's subject or its first line, `body` its text without the bot's
  mention, `author` the sender's display name (Graph gives no UPN here), `url` its `webUrl`, `item_type`
  `teams`, `state` always `New` and `labels` always the start label (Teams keeps no state of ours),
  `fields` `{channel, team_id, channel_id, conversation_id, activity_id, root_id, has_attachments}`.

## `tracker_list_candidates`

Input: `{"limit": 50}` (1-200). Output: `{"keys": ["mail:<newest>", "mail:<older>"]}`, newest first;
at most 10 pages of 50 Inbox messages are read. Without a mailbox: `{"keys": [], "reason": "…"}`.

## `tracker_parse_event`

Input: `{"headers": {"Authorization": "Bearer <JWT>", …}, "body": "<the activity>"}` — the platform
forwards `Authorization` because the declaration says `webhook.forward_authorization: true` (core ADR
0198).

Output: `{"verified": true, "events": [{"kind": "tracker.item.created", "item_key": "teams:…",
"delivery_id": "<activity id>"}]}`, or `{"verified": false, "events": []}`.

- **Verified** means: RS256, signed by a key of the Bot Framework's OpenID metadata (endorsed for
  `msteams` when the key lists endorsements), issuer `https://api.botframework.com`, audience the bot's
  app id, not expired (5 minutes of skew), and its `serviceurl` claim, when present, the activity's
  `serviceUrl`. No header, another scheme, a malformed body: not verified.
- **An event** is a `message` activity of Teams, in a configured `{team_id, channel_id}` of the tenant,
  with some text besides the bot's mention. A verified activity of another kind (a reaction, a
  conversation update, another channel) answers `verified: true` with no event.
- `delivery_id` is the activity id: a replayed delivery has the same, and the core de-duplicates on it.
  The core re-reads the item before acting; nothing else of the delivery is trusted.

## `tracker_set_state`, `tracker_upsert_status`, `tracker_close`

- `tracker_set_state {key, status, label, remove_labels}` → `{key, changed, state, ignored}`: the
  message's `Choregos:` category becomes `Choregos: <status>` (control characters, `,` and `;` replaced,
  at most 60 characters); the other categories are kept. `label` and `remove_labels` are reported in
  `ignored`. Without `status`, or with the current one, `changed: false`.
- `tracker_upsert_status {key, markdown, marker}` → `{key, written}`: the text is kept in the single-value
  extended property `String {793633a1-729c-497c-930e-276450befa92} Name choregos-status-<marker>` of the
  message — never sent, never shown to the requester; written only when it changed.
- `tracker_close {key, close_notes, close_code}` → `{key, closed, state}`: `Choregos: Done`, once
  (`closed: false` when already done). Nothing is sent to the requester; the notes are not kept.

## `tracker_comment` and `reply`

- `tracker_comment {key, markdown, action_key}` → `{key, id, written: false}`: **nothing is written**,
  whatever the key. The platform comments for its own readers (a question with a console link, a
  command's answer); none of it is the requester's. `id` is `comment:<reference>`, stable across replays.
- `reply {key, text, action_key, work_item_key}` → `{key, channel, id, sent}`: plain text, at most 4000
  characters (refused beyond). `action_key` and `work_item_key` are the platform's; when `work_item_key`
  is a key of this desk, `key` must be it.

Idempotence, per `action_key`:

- **Mail**: before sending, Sent Items is searched for a message carrying the single-value extended
  property `String {793633a1-729c-497c-930e-276450befa92} Name choregos-action` equal to the action key;
  found, nothing is sent (`sent: false`). Drafts is searched next: a draft carrying it (an attempt cut
  between `createReply` and `send`) is sent instead of a new one. Otherwise `createReply` → `PATCH` (the
  body as `text`, and the property, in one request) → `send`. Residual window: a replay between `send`
  and the moment Sent Items shows the message would send it twice.
- **Teams**: the reply carries a visible reference, `Ref: desk-<12 hex>` derived from the action key. A
  reply is not sent again when this process sent it (a bounded memory), when Graph shows a reply of the
  bot carrying that reference in the thread, or when the Bot Connector answers `409` for the activity id
  (the same reference; Teams is not known to keep client ids, so this is a last resort). Residual
  window: a new process (a restart, a second replica) replaying the action before Graph shows the first
  reply sends it twice.
