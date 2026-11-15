# Microsoft 365 plugin

One MCP server that reads Microsoft 365 through Microsoft Graph **as the person a call is made for**, with
their delegated permissions only. It is generic: any assistant binds a need to it; it ships no agent.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `microsoft-365` 0.1.0, core `>=0.26.0,<1.0.0` (the first core that sends a plugin's server the person's `user_consent` token, ADR 0135) |
| Server | [`servers/microsoft-365.yaml`](servers/microsoft-365.yaml), [`server/`](server) | three reads over Graph, and its `fake` for the eval matrix |
| Procedure | [`procedures/connector.mcp.microsoft-365.md`](procedures/connector.mcp.microsoft-365.md) | the Entra application, its consent, the connector's `user_consent` settings |
| Entra check | [`server/entra/`](server/entra), `python -m choregos_microsoft_365.entra` | the reference application of each stage, and the check that an application asks exactly its stage's permissions |

```sh
choregos plugin validate plugins/microsoft-365
choregos plugin pack plugins/microsoft-365 -o microsoft-365-0.1.0.tar.gz   # same bytes every time
```

`validate` reads the whole directory: run it on a clean tree (no `server/.venv` nor caches), as CI does.

## Identity: the person's, never the server's

The connector declares `identity_modes: [user_consent]`: each person connects their own Microsoft 365
account once in the console (`/me/connected-accounts`), the platform seals the refresh token in their
personal space and draws an access token for each call. It sends that token to the server in
`X-Choregos-User-Token`, next to the platform's own bearer token (`CHOREGOS_SERVER_TOKEN`), which
authenticates Choregos to the server.

- The person's token is the only credential the server sends to Graph, and only to
  `https://graph.microsoft.com/v1.0` (a next page elsewhere is refused; no redirect is followed).
- A tool call without it fails with `identity_unavailable` before anything reaches Graph. The server holds
  no secret, no client credential and no storage.
- The connector's configuration is schema-checked so it cannot widen the consent: `user_consent` only, one
  tenant's endpoints (never `common`), and the stage's scopes only.

## The operations

All three are reads. Every result carries `source: microsoft-graph` and `as_of`, and has an output schema.

| Operation | Stage | Graph permission | What it returns |
|:--|:--|:--|:--|
| `list_events_with(start, end \| within_hours, with_upn?, one_to_one_only?, newest_first?, limit?)` | 2 | `Calendars.Read` | the person's events in a window of at most 92 days; with another person (organizer or attendee); `one_to_one_only` keeps exactly two people, rooms aside |
| `search_mail_with(with_upn, since, limit?)` | 3 | `Mail.Read` | per thread: subject, date, sender and snippet of the latest message the other person sent or received |
| `search_chats_with(with_upn, since, limit?)` | 3 | `Chat.Read` | one-to-one and group chats the other person is a member of (not meeting chats): type, topic, member count, latest message's date, sender and snippet |

The window is `start` and `end`, not `from` and `to`: `from` is a reserved word of Python, which the MCP
SDK cannot serve as an argument name.

**Minimised on the server**, whatever Graph returns: at most 20 items (and 20 attendees per event); never a
full body, a snippet of at most 300 characters as plain text; never an attachment, nor whether there is
one; a message or chat is returned only when the other person takes part in it, checked on what Graph
returned (Graph's `$search` is fuzzy: a mail that only names the person in its text is dropped); a chat
never lists its members.

**Typed errors**, as `<code>: <why>`: `identity_unavailable` (no person's token, or Graph refused it),
`invalid_arguments` (refused before Graph), `forbidden` (a permission the tenant did not grant, a policy),
`not_found`, `rate_limited`, `upstream_error`. `account_not_connected` is the platform's own answer when
the person has not connected their account: the call never reaches the server.

## Stages

| Stage | Delegated permissions | Operations that answer |
|:--|:--|:--|
| 2 | `Calendars.Read`, `offline_access` | `list_events_with` (the others answer `forbidden`) |
| 3 | stage 2, `Mail.Read`, `Chat.Read` | all three |

Never an application permission. `python -m choregos_microsoft_365.entra --stage <2|3> app.json` checks the
application as Entra holds it (`az ad app show`) against its stage: exactly these permissions, single
tenant, the platform's callback as its only redirect URI, no implicit grant, no public client.

## The fake

`M365_FAKE=1` serves a seeded tenant (`example.test`) from memory, seeded relative to the hour it starts:
Camille's calendar, mail and chats, with Alex (two one-to-one meetings, a meeting of three, a thread, a
one-to-one and a group chat) and Sam (a one-to-one, a mail that names Alex but that Alex never received, a
chat without Alex). Any token reads Camille's data; `fake-expired` is refused (401), `fake-calendar-only`
is a stage-2 tenant (mail and chats answer `forbidden`). The server still refuses a call without a
person's token on the fake.

```sh
cd plugins/microsoft-365/server
uv sync --all-extras --dev
uv run ruff check . && uv run mypy --strict src && uv run pytest -q
M365_FAKE=1 CHOREGOS_SERVER_INSECURE_DEV=1 uv run choregos-microsoft-365   # local demo, port 8080
```
