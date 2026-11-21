# Access requests plugin

The requests a technical lead or an IT desk receives all day — "give me access to the CRM", "add Paul to
the finance group", "the expense tool fails since this morning" — become work items. An agent sorts each
one; the people a write belongs to decide it.

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `access-requests` 0.2.0, core `>=0.28.0,<1.0.0` (0.1.0 stays for older cores) |
| Template | [`templates/access-requests`](templates/access-requests/README.md) | requests from a trigger (any sender that posts JSON), internal tracker |
| Tests | [`tests/`](tests/test_template.py) | this template and its Microsoft 365 twin played against a core checkout (below) |

Its twin, `access-requests-m365` — requests from a shared mailbox and a Teams channel, every outcome
answered in the thread — is the [`microsoft-365-desk`](../microsoft-365-desk) plugin's, since its tracker
is that plugin's server. Both ship the same agent (`request-desk`), the same three skills
(`request-desk`, `access-catalogue`, `solutions`) and the same governed writes; a test keeps them from
drifting apart.

## What happens to a request

```
mailbox / Teams / trigger ──► access-request ──► request-desk agent (reads only)
   ├─ access ─► one access-grant per group ─► the tool's approvers decide ─► add_to_group  (─► "added" in the thread)
   ├─ bug ────► one bug-report ─► the desk approves ─► work_item.file into the solution's project (─► "filed")
   └─ other, or unsure ─► a person (─► the person's answer, in the thread)
```

- **The agent writes nothing.** It reads the request, the access catalogue and the solutions (skills the
  organisation edits) and the directory (`get_user`, bound to one sign-in name), and writes three outputs:
  `category`, `summary` and `plan` (`{grants, bugs}`). A guarantee (`output_schema`) refuses a plan the
  next step cannot read.
- **Each grant is one governed action**, `add_to_group` on the organisation's `directory` connector, with
  `remove_from_group` as its compensation. It waits for a member of the tool's approvers group —
  `access-approvers-<suffix>` in the identity provider, the catalogue's **Approvers** column naming the
  suffix — with the project's `approver` rank, signed in again in the last five minutes, whatever the
  organisation's policy allows for that operation (the core's ADR 0196). **The person it grants and the
  person who asked may not decide it**, organisation administrators included. A grant whose catalogue
  entry names no approvers is not proposed. The approver reads first what is written — the sign-in name
  and the group's object id — then the group name and tool the agent copied, `[sensitive]` when the
  catalogue says so, the reason, and who asked: the sender as the trigger or the tracker gives it, never
  a name the request text claims.
- **A bug is filed where its solution's team works.** The `solutions` skill names each solution's
  project; the core's `work_item.file` effect (ADR 0197) files the agent's report there through that
  project's own tracker — Jira, GitHub, GitLab or another — once a member of `it-desk` (never the
  reporter) approves it, reading the report first. The target project must accept work items from the
  desk's project (its config's `accepts_items_from`).
- **A refusal writes nothing.** A request whose grants were all refused, or that resolved none, and a bug
  report not filed, go to a person.
- **The request is data.** A sentence asking to skip an approval or to grant an unlisted group changes
  nothing: no write leaves the workflow without a person, and the agent reports it as a suspected prompt
  injection.
- **A person step waits 30 days.** Without a decision by then it ends as if rejected: a request is
  `declined`, a failed grant or filing is abandoned. The SLA (8 hours) only sends a reminder.

### With Microsoft 365 (`access-requests-m365`, in the `microsoft-365-desk` plugin)

The project's tracker is the `microsoft-365-desk` plugin's server: a message in the desk's shared mailbox
or Teams channel becomes a work item, and its sender is the requester. Every outcome is answered **in
the same thread** by its own action (`desk.reply`), after the write it reports, so a failed reply never
undoes a grant:

| Outcome | What the requester reads |
|:--|:--|
| a grant approved | "Done: `<sign-in name>` was added to the directory group `<object id>`." |
| a grant refused | "Not granted: … The IT desk will get back to you if needed." |
| a bug filed | "Thank you: your report was filed in the `<project>` backlog." |
| anything else | the answer a person of the desk wrote (a task); rejecting it declines without answering |

A reply carries fixed text, identifiers the workflow checked, or a person's words — never the agent's.
Replies wait for the organisation's policy of the `desk` connector's `reply` operation: `approval` by
default (a person clicks once more); an organisation that trusts its fixed texts sets it to `allowed`.

## Setting it up

1. **The directory connector** (organisation, once): Connectors → Identity → `entra`, named
   `directory`, scoped to an administrative unit (the core's procedure `connector.identity.entra`). Only
   accounts inside that unit can be granted.
2. **The approvers**: one identity-provider group per catalogue suffix, `access-approvers-<suffix>`,
   reaching the platform at sign-in (OIDC groups claim, or SCIM); its members need the `approver` rank on
   the project. Check that the platform sees the group by name, as written here.
3. **The catalogue and the solutions**: publish new versions of the `access-catalogue` and `solutions`
   skills in the organisation. Each solution's project lists the desk's project in its config's
   `accepts_items_from`.
4. **The desk**: the identity-provider group `it-desk` approves bug reports and answers what the agent
   does not sort; its members need the `contributor` rank.
5. **The intake**, either:
   - `access-requests`: a trigger on the project (the core's `docs/usage.md`, "Work items opened by
     another system"), one work item per message, fed by anything that POSTs JSON — a Power Automate flow
     from a channel or a mailbox, with the trigger's token as a secure input:

     ```yaml
     triggers:
       - name: request
         title: "{/subject}"
         body: "{/text}"
         key: /message_id
         fields: { requester: /from, channel: /channel, source_ref: /link }
         workflow: access-request
     ```

   - `access-requests-m365`: install and configure the `microsoft-365-desk` plugin (its procedure: the
     Entra application restricted to the shared mailbox, the Azure Bot, the Teams app), then create the
     project from `plugin:microsoft-365-desk/access-requests-m365`.

## What it does not do yet

- **Treat a sensitive group with more than its own approvers.** One rule per grant: give a sensitive
  group a stricter approvers group in the catalogue.
- **Keep grants to what the agent resolved.** Anyone who may create a work item in the project can open
  an `access-grant` by hand, past the agent and the catalogue; it waits for the same approval, with the
  same exclusions.
- **Check the copied labels against the catalogue.** The group name, the tool, `[sensitive]` and the
  approvers suffix are the agent's copy of the catalogue; the group id is what is written, and the
  approver reads it first. The suffix only chooses among `access-approvers-*` groups, and the rank is
  still asked.
- **Compare the excluded people by more than their address.** The person granted is excluded by sign-in
  name, the requester by the address the trigger or the tracker gives: an approver whose platform e-mail
  differs from both is not recognised as either.
- **Check who may ask for whom beyond the catalogue's column.** The directory read gives no manager and
  no membership, and is not limited to the administrative unit.
- **Retry a reply forever.** A reply that fails twice is dropped; the action log shows it.

## Tests

`tranibor plugin validate` reads a template's manifest only. `tests/test_template.py` reads what both
templates ship the way the platform does: the core's own template conformance checks, the routing after
the agent, the outputs against the guarantee, the fan-outs' child fields against the child workflows'
inputs, every governed write rendered as proposed with the approval it waits for, the replies' texts,
and the drift between the two templates. From a core checkout:

```sh
cd <core checkout> && uv sync --frozen
uv run --frozen pytest -q -p no:cacheprovider <this repo>/plugins/access-requests/tests
```

CI's `templates` job runs it at `CHOREGOS_REF`: it needs the core with ADRs 0196 and 0197, so that pin
moves with the core release that ships them.
