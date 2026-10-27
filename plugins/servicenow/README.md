# ServiceNow plugin

Triage ServiceNow incidents with a Choregos agent, under human approval. This directory holds the
plugin's **MCP server** (`server/`) and its descriptor (`servers/servicenow.yaml`); the agent, its
template, evals and procedures come in a separate change.

## What the server does

An MCP server (streamable HTTP at `/mcp`) over the ServiceNow **Table API**:

| Tools | Access | Notes |
|:--|:--|:--|
| `search_records`, `get_record`, `get_journal`, `get_ci`, `list_ci_relations`, `search_knowledge`, `similar_incidents` | read | `search_knowledge`: published articles only; `similar_incidents`: resolved or closed in the last 90 days, same CI or same terms |
| `add_work_note` | write | internal; idempotent through a `[choregos:<key>]` marker |
| `update_record` | write | category, subcategory, impact, urgency, assignment group, assignee, CI — **never priority**; returns the previous values, its compensation restores them |
| `add_comment` | write, **a person approves** | the caller sees it |
| `resolve_incident` | write, **a person approves** | compensation: `reopen_incident` |
| `reopen_incident` | write | undoes a resolution |
| `create_change_request` | write, **a person approves** | idempotent through `correlation_id` |
| `tracker_*` (8 tools) | read / write | the tracker profile, see [server/TRACKER_PROFILE.md](server/TRACKER_PROFILE.md) |

It also serves `GET /healthz` and `POST /events`, the webhook a ServiceNow Business Rule posts to (the
shared secret in `X-Choregos-Secret`, compared in constant time; answers
`[{kind, item_key, delivery_id}]` with `delivery_id = sys_id:sys_mod_count`).

The rules it enforces before any call to ServiceNow:

- a **closed list of tables**: `incident`, `sc_req_item`, `change_request`, `problem`, `kb_knowledge`,
  `cmdb_ci`, `cmdb_rel_ci`, `sys_user_group`, `sys_choice`, `sys_journal_field`; writes on the first four only;
- **typed filters** (`{field, op, value}`) turned into an encoded query; a raw `sysparm_query` is never
  accepted. Values have `^` escaped as `^^`, and `javascript:` and control characters are refused, so a
  term like `x^ORactive=false` cannot change the query;
- every read names its fields (`sysparm_fields`), asks for `sysparm_display_value=all` (each record has
  `fields` with stored values and `display` with labels) and returns at most 50 rows;
- state codes come from `sys_choice` (cached for an hour), not from constants.

Governance is the platform's: `servers/servicenow.yaml` lists every tool with its access, the three
writes that always need a person, the compensations, and the only host the server may reach
(`egress.from_fields: [base_url]`). A tool not listed there is refused by the platform.

## Configuration

Environment variables (the platform fills them from the descriptor's `config_schema`; `secret_fields`
are sealed):

| Variable | Meaning |
|:--|:--|
| `SERVICENOW_BASE_URL` | `https://<instance>.service-now.com` |
| `SERVICENOW_AUTH` | `oauth` (client credentials, default) or `basic` (a dedicated integration user: older releases, PDIs) |
| `SERVICENOW_CLIENT_ID`, `SERVICENOW_CLIENT_SECRET` | OAuth inbound integration (`auth=oauth`); the token is cached until shortly before it expires and renewed once on a 401 |
| `SERVICENOW_USERNAME`, `SERVICENOW_PASSWORD` | the integration user (`auth=basic`); give it a custom role, never `admin` |
| `SERVICENOW_WEBHOOK_SECRET` | the secret the Business Rule sends; without it, `/events` refuses everything |
| `SERVICENOW_ASSIGNMENT_GROUPS` | comma-separated group names or sys_ids whose queue `tracker_list_candidates` polls |
| `SERVICENOW_FAKE` | `1` for the fake instance (below) |
| `PORT` | listening port, `8080` by default |

## Fake mode

`SERVICENOW_FAKE=1` puts a seeded in-memory instance under the real client (as an `httpx` transport),
so the encoded queries, field lists and authentication follow the production path. No variable other
than `SERVICENOW_FAKE` is needed; the webhook secret defaults to `fake-webhook-secret` and the queue to
`Service Desk`. The seed:

- twelve incidents: a duplicate pair (`INC0010001`/`INC0010002`, mobile mail), a vague one (`INC0010003`),
  a VIP caller (`INC0010004`, the CFO), one whose description carries a prompt injection (`INC0010005`),
  resolved ones that `similar_incidents` finds (and one too old to be found);
- a request item, a change, a problem; CIs with relations; knowledge articles in the published, draft
  and retired states; groups; the `sys_choice` states, impacts, urgencies and priorities.

## Run locally

```sh
cd plugins/servicenow/server
uv sync --dev
SERVICENOW_FAKE=1 uv run choregos-servicenow          # http://localhost:8080/mcp
curl localhost:8080/healthz
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict src && uv run pytest -q
```

Against a real instance (a Personal Developer Instance works):

```sh
SERVICENOW_BASE_URL=https://dev12345.service-now.com SERVICENOW_AUTH=basic \
SERVICENOW_USERNAME=choregos.integration SERVICENOW_PASSWORD=… \
SERVICENOW_WEBHOOK_SECRET=… uv run choregos-servicenow
```

The image: `docker build -t servicenow-mcp plugins/servicenow/server` (non-root, port 8080, exec-form
entry point). CI builds, signs and publishes it and writes its digest into `servers/servicenow.yaml`.
