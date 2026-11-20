# Access requests plugin

The requests a technical lead or an IT desk receives all day — "give me access to the CRM", "add Paul to
the finance group", "the expense tool fails since this morning" — become work items. An agent sorts each
one; a person decides every write.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `access-requests` 0.1.0, core `>=0.27.14,<1.0.0` |
| Template | [`templates/access-requests`](templates/access-requests/README.md) | two workflows, one agent, two skills, template-local |
| Tests | [`tests/`](tests/test_template.py) | the template played against a core checkout (below) |

## What happens to a request

```
chat channel / mailbox ──► trigger ──► access-request ──► request-desk agent (reads only)
                                         ├─ access ─► one access-grant per group ─► a project owner approves ─► add_to_group
                                         ├─ bug ────► a qualified report ─► a person files it
                                         └─ other, or unsure ─► a person
```

- **The agent writes nothing.** It reads the request, the access catalogue (a skill the organisation
  edits) and the directory (`get_user`), and writes three outputs: `category`, `summary` and `plan`. A
  guarantee (`output_schema`) refuses a plan the next step cannot read.
- **Each grant is one governed action.** `add_to_group` on the organisation's `directory` connector, with
  `remove_from_group` as its compensation. It waits for a project owner (or an organisation
  administrator) who signed in again in the last five minutes, whatever the organisation's policy allows
  for that operation. The approver reads first what is written — the sign-in name and the group's object
  id — then the group name and tool the agent copied, `[sensitive]` when the catalogue says so, the
  reason, and who asked: the sender the trigger mapped, never a name the request text claims.
- **A refusal writes nothing.** A request whose grants were all refused, or that resolved none, goes to
  a person, who tells the requester.
- **The request is data.** A sentence asking to skip an approval or to grant an unlisted group changes
  nothing: no write leaves the workflow without a person, and the agent reports it as a suspected prompt
  injection. Its one directory read is bound to a sign-in name.
- **A person step waits 30 days.** Without a decision by then it ends as if rejected: a request is
  `declined`, a bug report goes back to the desk, a failed grant is abandoned. The SLA (8 hours) only
  sends a reminder.

## Setting it up

1. **The directory connector** (organisation, once): Connectors → Identity → `entra`, named
   `directory`, scoped to an administrative unit (the core's procedure `connector.identity.entra`). Only
   accounts inside that unit can be granted.
2. **The project**: install the plugin, then create a project from the template
   `plugin:access-requests/access-requests` and give it the internal tracker. Keep the project's owners to
   the people who may approve grants: every owner can.
3. **The catalogue**: publish a new version of the `access-catalogue` skill in the organisation with your
   tools, their directory groups' object ids, which are sensitive, and which a colleague may ask for.
4. **The people**: the desk steps (bug reports, requests for a person, failed grants) go to the identity
   provider group `it-desk`; an organisation administrator may always answer them. Grants wait for a
   project owner or an organisation administrator.
5. **The intake**: a trigger on the project (the core's `docs/usage.md`, "Work items opened by another
   system"), one work item per message:

   ```yaml
   triggers:
     - name: request
       title: "{/subject}"
       body: "{/text}"
       key: /message_id
       fields: { requester: /from, channel: /channel, source_ref: /link }
       workflow: access-request
   ```

   Mint the trigger's token (shown once). Anything that can POST JSON with it feeds the trigger. With
   Microsoft 365, a Power Automate flow ("when a message is posted in a channel" or "when a new email
   arrives in a shared mailbox") posts those fields to the trigger's URL, the token in its
   `Authorization: Bearer` header, kept as a secure input of the flow. A sender that can compute an
   HMAC uses `auth: signature` instead. People write to that channel or mailbox rather than to one
   person's chat.

## What it does not do yet

- **Answer the requester.** The platform has no effect that posts back into the original chat or mail
  thread; a person tells the requester, or the requester follows the work item.
- **Route an approval to the tool's owner.** An action's approvers are fixed in the workflow, so every
  grant waits for a project owner; the catalogue's owner cannot be the approver. An organisation that
  needs a narrower set adds a `group` to the approver rule of `access-grant`.
- **Treat a sensitive group differently.** The approval is the same for every grant; `[sensitive]` is in
  the title the approver reads.
- **Keep a person from approving their own grant.** A grant is proposed by the platform, not by the
  requester, so separation of duties excludes nobody: a project owner can approve a grant they asked
  for, or one that names them.
- **Keep grants to what the agent resolved.** Anyone who may create a work item in the project can open
  an `access-grant` by hand, past the agent and the catalogue; it waits for the same approval.
- **Check the copied labels against the catalogue.** The group name, the tool and `[sensitive]` are the
  agent's copy of the catalogue; the group id is what is written, and the approver reads it first.
- **Check who may ask for whom beyond the catalogue's column.** The directory read gives no manager and
  no membership. It is not limited to the administrative unit either: the agent can read any account of
  the tenant by its sign-in name, and what it writes in `summary` is read by the project's members.

## Tests

`tranibor plugin validate` reads a template's manifest only. `tests/test_template.py` reads what the
template ships the way the platform does: the core's own template conformance checks, the routing after
the agent, the agent's outputs against the guarantee, the child fields the fan-out renders against the
child workflow's inputs, and the grant rendered as a proposed action with the approval it waits for.
From a core checkout:

```sh
cd <core checkout> && uv sync --frozen
uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/access-requests/tests
```

CI's `templates` job runs it at `CHOREGOS_REF`.
