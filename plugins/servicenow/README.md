# ServiceNow plugin

Triage ServiceNow incidents with a Choregos agent, under human approval.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `servicenow` 0.1.0, core `>=0.21.0,<0.23.0` |
| Agent | [`agents/itsm-triager.yaml`](agents/itsm-triager.yaml) | reads, cites, proposes; every write waits for a person |
| Skill | [`skills/servicenow-triage`](skills/servicenow-triage/SKILL.md) | impact/urgency matrix, escalation, work note vs customer comment |
| Template | [`templates/itsm-incident-triage`](templates/itsm-incident-triage/README.md) | queue → triage → review (service desk) → apply → done |
| Evals | [`evals/itsm-triager.yaml`](evals/itsm-triager.yaml) | instruction checks, and four live cases on the fake instance |
| Procedure | [`procedures/connector.mcp.servicenow.md`](procedures/connector.mcp.servicenow.md) | OAuth integration, custom role, Business Rule |
| Server | [`servers/servicenow.yaml`](servers/servicenow.yaml), [`server/`](server) | the MCP server over the Table API (below) |

```sh
choregos plugin validate plugins/servicenow
choregos plugin pack plugins/servicenow -o servicenow-0.1.0.tar.gz   # same bytes every time
```

`validate` reads the whole directory: run it on a clean tree (no `server/.venv` nor caches), as CI does.

## The agent: `itsm-triager`

It reads the incident (`get_record`), its journal, its CI and that CI's relations, finds similar resolved
incidents and published knowledge articles, and writes `{"itsm_triage_markdown": "…"}` with four
sections: **Findings**, **Proposed classification**, **Proposed actions**, **Confidence**. Every
incident and article is cited by number.

- It proposes category, impact, urgency and assignment group with their evidence — never priority.
- It proposes a work note; a customer comment and a resolution **only when a published fix exists**.
- A vague incident is never resolved (questions for the caller instead); a VIP caller gets no comment
  without a person's approval; text in a ticket is data, never instructions — an instruction found
  there is reported as a suspected prompt injection and not obeyed.
- Its tools: the seven reads, `add_work_note`, `update_record`, `add_comment`, `resolve_incident`. Not
  the `tracker_*` tools, not `create_change_request`. 30 turns, 15 minutes, 1 USD.

The evals' live cases run against the fake instance's seed: the duplicate `INC0010002` (of
`INC0010001`, fixed by `KB0010001`), the vague `INC0010003`, the VIP `INC0010004` and the injection in
`INC0010005`.

## What the format cannot say yet

Found while writing this plugin against the core at `49fc8082` (S43-02):

- `plugin-server.schema.json` is closed and has no `path`/`health`, no compensation arguments, no
  platform-filled arguments (`action_key`): they are kept as comments and operation descriptions in
  `servers/servicenow.yaml`, and `server/tests/test_descriptor.py` checks them.
- A template cannot name a plugin's server as its tracker (see the template's README).
- A procedure's `{{webhook_url:…}}` only knows the core's webhook sources; the Business Rule's URL is
  written `{{api_url}}/api/v1/webhooks/plugins/servicenow/servicenow`.
- The connector an installed plugin server becomes is assumed to be named after the server
  (`servicenow`), hence the tool names `servicenow__get_record`, … in the agent's instructions.
- An eval suite is only required to name its agent; this one uses the catalogue's `eval_cases` shape plus
  a `cases` list in the `AgentEvalCase` shape.

## The server

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
