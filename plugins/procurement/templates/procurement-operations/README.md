# procurement-operations — onboard a supplier, a record created under approval

One workflow, `supplier-onboarding`, for a purchasing team that wants a supplier record created only after a
person reviewed a file whose completeness a job measured, and after two people approved the write.

It needs the organisation connector `purchasing` (the plugin's MCP server, installed with the plugin). The
comparison of offers, `procurement-sourcing`, does not: it stays usable without it.

The work item takes one to ten files (`pdf`, `docx`) and four fields: `legal_name`, `registration_number`,
`country` (two letters) and `category` (lower-case words joined by hyphens), all required.

| Step | Who | What happens | Gates | On failure |
|:--|:--|:--|:--|:--|
| `t-extract` | job `extract` | converts each file to text | `outputs_present`; measured `files_read` >= 1, `files_unreadable` = 0, `files_truncated` = 0 | a person (`needs_human`) |
| `t-file` | agent `supplier-file-checker` | copies the identity and each dated document into `supplier_file` | `output_schema` (the job image's `supplier_file` schema) | back to `extracted` twice, then a person |
| `t-check` | job `supplier-check` | measures what is missing, expired, different from the fields, already held | measured `documents_missing`, `documents_expired`, `identity_mismatch`, `duplicates_found`, each = 0 | a person, with `supplier_check` listing each finding |
| review | group `supplier-data` (48 h) | reviews the file and the check, attested; or declines with a reason | the attestation | |
| `t-create` | the platform | one governed action, `procurement.create_supplier`, calls `purchasing.create_supplier` | approval, below | back to `approved` twice, then a person |

## The default `--required` kinds, and editing them

The job `supplier-check` is given the kinds of document the file must hold, in its command in the workflow
file (never a field: a field can be edited by the person the requirement constrains). The defaults:

- `company_registration`: an extract or certificate of the company's registration;
- `tax_clearance`: a certificate of tax or social-contribution compliance;
- `liability_insurance`: a certificate of liability insurance.

An organisation edits this list in its own copy of the workflow, one `--required` kind per entry. The
agent writes each document's `kind` as a lower-case label with words joined by underscores, and the job
compares after ignoring case, spaces and punctuation: a kind an organisation adds must be one the agent
would write for that document (the agent names the five kinds above and uses a short label of its own for
any other), so check a new kind against a real file before relying on it. A kind the agent writes
otherwise counts as missing, which stops the work item at a person: it errs on the safe side.

## The approval of the creation

The action is approved by people of the group `supplier-data`, rank `approver` or above, **two of them**
(`min: 2`), each re-authenticated within 10 minutes, and `separation_of_duties` is on. The person who
reviewed the file belongs to the same group, so they cannot be the only approver: a second person must
decide. Nobody decides twice. The arguments of `create_supplier` are the supplier's name, registration
number and country, which the check proved equal to what the documents state, and the category: no other
field, and **no bank detail is handled** by the workflow, the agent, the job or the server's profile.
Changing a supplier's bank account stays a human process. A creation the purchasing system refuses as a
duplicate stops at a person.

## What the workflow guarantees

- The agent dates documents and judges none: a document is expired because a job compared its date with
  today's, and any finding stops at a person on measured facts only.
- Nothing is written to the purchasing system before a review and a two-person approval.
- The only action is the creation; it has a compensation (archiving the record) declared by the server.

## What it does not guarantee (ADR 0198)

- **No OCR.** A scanned document is not read; it counts in `files_unreadable` and stops at a person.
- **No authentication of documents.** The check measures presence, dates, identity and duplicates; that a
  certificate is genuine is the reviewer's judgment.
- **No injection detector.** The protection is mechanical: the agent holds no tool, no web and no memory,
  its output is held to a schema, nothing is written before two approvals.
- **The duplicate check is what the connector finds**: a supplier held under another spelling of the name
  and another registration number is not found.
- **Measured does not mean true.** A document that states a wrong registration number, copied faithfully,
  is caught only if it differs from what the person typed.

## The renewal campaign: one memo and one decision per contract ending soon

Two more workflows, `contract-renewals` (the campaign) and `contract-renewal-memo` (one contract). They need the
same connector `purchasing` and the agent `renewal-analyst`, and a group `buyers`.

| Step | Who | What happens | Gates | On failure |
|:--|:--|:--|:--|:--|
| `t-list` | job `renewals` | lists the contracts that end within 90 days, at most 50, into `contracts` (`expiring`, `by_id`, `left_out`) | `outputs_present` | a person (`needs_human`) |
| `t-measure` | the platform | goes on only if the measured `contracts_expiring` is at least 1 | `evidence_facts`, measured only | one more listing, then the terminal `nothing_due` |
| `t-fan-out` | the platform | one child `contract-renewal-memo` per expiring contract, at most 50, and waits for every one | the join | a person |
| `t-close` | group `buyers` | reads the children and `contracts.left_out`, writes the closing note, attested | the attestation | |

Each child: the agent `renewal-analyst` reads the contract (`get_contract` only) and writes
`renewal_memo_markdown`, gated by `output_sections` (five sections, in order) and by `numbers_measured` against
`parent.contracts.by_id.$contract_id`, the summary the job measured for that contract and no other. A person of
`buyers` decides `renew`, `renegotiate` or `terminate` in an attested task. The decision is recorded by one
governed action, `purchasing.add_contract_note`, whose text names the decision and the person's rationale; a
person of `buyers` approves it, re-authenticated within 10 minutes. The workflows write nothing else.

### The schedule is project configuration

A schedule opens the campaign; it is not template content (`ProjectConfig.schedules`, core ADR 0113). Add it to
the project's configuration:

```yaml
schedules:
  - name: contract-renewals
    cron: "0 6 1 * *"          # UTC, five fields: the first of each month at 06:00
    title: Contract renewals
    workflow: contract-renewals
```

The window (90 days) and the cap (50) are arguments of the job in the workflow file, never fields: edit them in
your own copy of the workflow, and keep `max_children` equal to `--max`.

### What the campaign guarantees

- A contract ending soon gets a memo whose every number is one the job measured for that contract, and a
  decision a named person attested; nothing is written to the purchasing system without a buyer's approval.
- An empty list ends the campaign in `nothing_due` on a measured count; a failed listing goes to a person and is
  never read as "nothing due".
- A contract that does not fit under the cap is named in `contracts.left_out`, never dropped.

### What it does not guarantee

- **The closing summary is a buyer's.** Nothing checks that the closing note quotes `left_out`: the task asks for
  it. The platform has no step that writes prose from a job's facts without an agent, and the campaign adds none.
- **Every campaign lists the same contract again** until it has ended or is outside the window: a monthly
  schedule opens a second memo for a contract decided last month. The earlier decision is a note on the contract
  (`note_count`); the job does not filter on it.
- **A memo does not decide.** The agent lists what the file shows for each option and recommends none; a note
  or a figure that only `get_contract` holds is described in words, since the gate admits only the summary's.
- **No injection detector**: a contract's notes are third-party text. The agent is told to report an instruction,
  the number gate bounds what the memo can claim, no write happens before a person decides and another approves.

## The second workflow: `purchase-request`, a request qualified and routed

An employee states a need (`need`, required), and optionally an estimated amount (a number), its currency
(three letters) and the date it is needed by. The agent `request-qualifier` reads the request against the
framework agreements the purchasing server lists and the knowledge base `purchasing-policy`, and proposes one
route: `catalogue`, `framework_agreement`, `three_quotes` or `tender`. A person of the group `buyers` reviews,
attested, and **chooses the route** (one task per route, so the buyer may differ from the proposal); the
terminal state names it (`routed_catalogue`, `routed_framework_agreement`, `routed_three_quotes`,
`routed_tender`), or the buyer declines, or sends the proposal back with remarks. **The workflow opens nothing
in the purchasing system**: launching a consultation or an order is the buyer's next gesture.

| Step | Who | Gates |
|:--|:--|:--|
| `t-qualify` | agent `request-qualifier` | `output_schema` (`qualification`: `category`, `route`, `agreement_id`, `rationale`; an agreement id for a framework agreement and for no other route); `outputs_in` on `route`; `numbers_measured` on `fields`; `tool_called` on `purchasing__list_framework_agreements` |
| review | group `buyers` (48 h) | the attestation |

- **The policy is the organisation's.** The template declares the base `purchasing-policy` empty (no source,
  served as files): add your own documents to it (categories bought from a catalogue, the amounts that call for
  quotes or a tender). The agent applies the rules as written, never invents a threshold, and with no rule that
  covers the request proposes the most demanding route, `tender`, which the buyer may lower.
- **The agreements are always looked at.** A gate on a transition applies to every run of it, so it cannot
  wait for the route: the agent must call `list_framework_agreements` every time, and the ledger proves it.
- **The amount is the one typed.** Every number of the brief and the qualification must be in the work item's
  fields. A request with no amount makes the agent ask (`ask_human`; the work item waits at `needs_human` for
  an answer), never guess. An amount that reaches the agent only as an answer is not a field, so the agent
  writes it without digits.
- **Limits that follow.** The agent writes no threshold of the policy and no figure of an agreement (they are
  not measured): it cites the policy by the title of its section. An agreement id whose number is ten or more
  (`FA-0012`) is read by the gate as an unmeasured number and refused, so the proposal goes to a person; the
  ids of the fake's scenes stay below ten.
- **Not proved**: what a model writes, or that the route is right: the evals are live and CI does not play them.
