# Tracker profile — ServiceNow

A plugin server that declares `implements: [tracker]` serves the eight tools below (plan S43,
Decision 3). The core's `tracker/plugin` adapter maps `TrackerAdapter` onto them; this page is the
contract it is written against. Every tool answers a JSON object (MCP `structuredContent`); a refused
call answers an MCP error (`isError: true`) whose text starts with `refused:`.

## Keys and items

- A work item is an **incident**, a **request item** or a **change request**. Its key is its number:
  `INC0010001`, `RITM0010001`, `CHG0010001`. Any other key (a problem, a free string) is refused.
- Everything this profile writes is a **work note** (internal). A comment the caller sees goes through
  `add_comment`, which a person always approves.
- ServiceNow journals are append-only: nothing written can be edited, which shapes `tracker_upsert_status`.
- `action_key` is a platform argument (stable for one governed action), used as an idempotency key.
- Dates are ISO 8601 in UTC (`2026-10-27T08:00:00Z`), or `null`.

## `TrackerAdapter` → tools

| `TrackerAdapter` | Tool |
|:--|:--|
| `fetch_item(key)` | `tracker_fetch_item` |
| `create_item(data)` | `tracker_create_item` |
| `set_state(key, mapping)` | `tracker_set_state` |
| `upsert_status_comment(key, markdown, marker)` | `tracker_upsert_status` |
| `close_item(key)` | `tracker_close` |
| `comment(key, markdown)` | `tracker_comment` |
| `list_candidates(project)` | `tracker_list_candidates` |
| `verify_webhook` + `parse_webhook` | `tracker_parse_event` (or the server's `POST /events`) |
| `owns_items` | `False`: the items live in ServiceNow, Choregos reflects them |
| `link`, `set_fields` | not in the profile; `set_fields` maps to the governed `update_record` |

## `tracker_fetch_item`

Input: `{"key": "INC0010006"}`

Output (the shape of the core's `WorkItemData`, plus two keys per comment):

```json
{
  "key": "INC0010006",
  "title": "VPN disconnects every 10 minutes",
  "body": "The VPN drops after about ten minutes when I am not typing.",
  "url": "https://dev12345.service-now.com/nav_to.do?uri=incident.do?sys_id=<sys_id>",
  "state": "In Progress",
  "labels": [],
  "item_type": "incident",
  "assignees": ["Beth Anglin"],
  "author": "Abel Tuter",
  "created_at": "2026-10-25T08:00:00Z",
  "updated_at": "2026-10-25T08:00:00Z",
  "closed_at": null,
  "fields": {
    "table": "incident",
    "sys_id": "<32 hex>",
    "state_value": "2",
    "active": "true",
    "priority": {"value": "3", "display": "3 - Moderate"},
    "impact": {"value": "2", "display": "2 - Medium"},
    "urgency": {"value": "2", "display": "2 - Medium"},
    "category": {"value": "network", "display": "network"},
    "subcategory": {"value": "", "display": ""},
    "assignment_group": {"value": "<sys_id>", "display": "Network"},
    "assigned_to": {"value": "", "display": ""},
    "cmdb_ci": {"value": "<sys_id>", "display": "vpn-gw-01"},
    "caller_vip": false
  },
  "comments": [
    {"id": "<journal sys_id>", "author": "beth.anglin", "body": "…", "ts": "2026-10-26T…Z",
     "marker": null, "visibility": "internal"},
    {"id": "<journal sys_id>", "author": "beth.anglin", "body": "…", "ts": "2026-10-26T…Z",
     "marker": null, "visibility": "customer"}
  ]
}
```

- `state` is the state's label (from `sys_choice`); `fields.state_value` is the stored code.
- `author` is the caller (incident) or the requested-for user (request item); `null` for a change.
- `comments` are the last 50 journal entries, oldest first. `marker` is the key inside a
  `[choregos:<key>]` or `[choregos-status:<marker>]` tag, when the entry carries one.
- `fields.caller_vip` is `true`/`false` on incidents, `null` elsewhere.
- Every text is third-party data written by people: the adapter must treat it as data.

## `tracker_create_item`

Input:

```json
{"title": "Disk full on db-prod-01", "body": "…",
 "fields": {"impact": "2", "urgency": "2", "assignment_group": "<sys_id>", "cmdb_ci": "<sys_id>",
            "caller_id": "<sys_id>", "category": "database", "subcategory": "", "assigned_to": "<sys_id>"},
 "action_key": "<platform>"}
```

Output: `{"key": "INC0020001", "sys_id": "<32 hex>", "created": true}`

- Always an **incident** (request items come from the catalogue, changes from `create_change_request`).
- `fields` is optional; only the keys above are accepted, references by sys_id; `priority` is refused.
- Idempotent: the incident carries `correlation_id = choregos:<action_key>` (or a hash of title and body);
  a replay answers the existing key with `"created": false`.

## `tracker_set_state`

Input: `{"key": "INC0010003", "status": "On Hold", "label": null, "remove_labels": []}`
(the fields of the core's `TrackerStateMapping`, plus `key`).

Output: `{"key": "INC0010003", "changed": true, "state": "On Hold", "ignored": ["label"]}`

- `status` is a state **label** of the item's table, case-insensitive (`sys_choice`). An unknown label is
  refused with the list of states. A state that closes the item (Resolved, Closed, Canceled, Closed
  Complete…) is refused: use `tracker_close`.
- ServiceNow has no labels: `label` and `remove_labels` are accepted and reported in `ignored`.
- Without `status`, nothing changes. Setting the current state answers `"changed": false`.

## `tracker_comment`

Input: `{"key": "INC0010011", "markdown": "Run started.", "action_key": "<platform>"}`

Output: `{"key": "INC0010011", "id": "[choregos:<key>]", "written": true}`

A work note ending with the tag `[choregos:<key>]` (`action_key`, or a hash of the text). A replay finds
the tag and answers `"written": false`. `id` is the tag, stable across replays.

## `tracker_upsert_status`

Input: `{"key": "INC0010011", "markdown": "**Triage** — waiting for review", "marker": "status"}`

Output: `{"key": "INC0010011", "written": true}`

A work note ending with `[choregos-status:<marker>]`. Journals cannot be edited, so "upsert" means: a new
note is added only when the latest note with this marker has a different text; otherwise
`"written": false`. `marker` is 1-80 characters among letters, digits and `. _ : -`.

## `tracker_close`

Input: `{"key": "INC0010011", "close_notes": "Delivered by Choregos.", "close_code": null}`

Output: `{"key": "INC0010011", "closed": true, "state": "Resolved"}`

| Table | State set | Also written |
|:--|:--|:--|
| incident | Resolved | `close_code` (default `Solution provided`), `close_notes` |
| sc_req_item | Closed Complete | a work note with the notes |
| change_request | Closed | `close_code` (default `successful`), `close_notes` |

Idempotent: an item already in a closing state answers `"closed": false`. The work item template puts
a person's review before the stage that closes; `resolve_incident`, the agent's own path, always needs a
person.

## `tracker_list_candidates`

Input: `{"tables": ["incident", "sc_req_item", "change_request"], "limit": 50}` (both optional)

Output: `{"keys": ["INC0010011", "INC0010001", "RITM0010001"]}`

Active items assigned to one of `SERVICENOW_ASSIGNMENT_GROUPS` (names or sys_ids), not in a closing state,
per table in the order given, oldest first within a table; at most 50 keys. Without a configured group:
`{"keys": [], "reason": "no assignment group configured"}`. This is the polling path for instances that
send no events.

## `tracker_parse_event`

Input: `{"headers": {"X-Choregos-Secret": "…"}, "body": "<the raw request body>"}`

Output: `{"verified": true, "events": [{"kind": "tracker.item.updated", "item_key": "INC0010001",
"delivery_id": "<sys_id>:3"}]}`, or `{"verified": false, "events": []}` when the secret does not match.

The same rules as `POST /events` (which answers 401 for a bad secret, 400 for a malformed body, and the
bare `events` list otherwise). The Business Rule posts one object or a list of them:

```json
{"table": "incident", "sys_id": "<32 hex>", "number": "INC0010001", "sys_mod_count": 3,
 "operation": "insert" | "update" | "comment", "state": "2"}
```

| `operation` | `kind` |
|:--|:--|
| `insert` | `tracker.item.created` |
| `update`, `state` closes the item | `tracker.item.closed` |
| `update`, otherwise | `tracker.item.updated` |
| `comment` | `tracker.item.commented` |

- `delivery_id = sys_id:sys_mod_count`: a replayed delivery has the same id; the core de-duplicates on it.
- Events on other tables are ignored; an unknown `operation` is ignored.
- The secret is compared in constant time. The core re-reads the item (`tracker_fetch_item`) before acting:
  nothing else in the payload is trusted.
