# Procurement plugin

Compare supplier offers with agents that are never trusted with arithmetic. This is the content half of
the plugin of the Tranibor core's ADR 0198 (`procurement is a plugin whose code half is a job image`).

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `procurement` 0.2.0, core `>=0.28.0,<1.0.0` (provisional floor, below) |
| Agent | [`agents/offer-reader.yaml`](agents/offer-reader.yaml) | catalogue role `structure`: copies the offers into `offers_structured` |
| Agent | [`agents/sourcing-analyst.yaml`](agents/sourcing-analyst.yaml) | catalogue role `compare`: writes `comparison_memo_markdown` |
| Agent | [`agents/supplier-file-checker.yaml`](agents/supplier-file-checker.yaml) | catalogue role `structure`: copies a supplier's identity and dated documents into `supplier_file` |
| Agent | [`agents/renewal-analyst.yaml`](agents/renewal-analyst.yaml) | catalogue role `renewal_memo`: writes `renewal_memo_markdown` for one contract ending soon (`get_contract` only) |
| Workflows | [`templates/procurement-operations`](templates/procurement-operations/README.md) | `contract-renewals` (a scheduled campaign) and `contract-renewal-memo` (one memo and one buyer's decision per contract, recorded as a governed contract note) |
| Agent | [`agents/request-qualifier.yaml`](agents/request-qualifier.yaml) | catalogue role `qualify`: reads a purchase request against the house policy and the framework agreements, proposes a route in `qualification` and a `brief_markdown` |
| Skills | [`skills/reading-supplier-offers`](skills/reading-supplier-offers/SKILL.md), [`skills/writing-comparison-memos`](skills/writing-comparison-memos/SKILL.md) | the output contract and rules of each agent |
| Template | [`templates/procurement-sourcing`](templates/procurement-sourcing/README.md) | workflow `offer-comparison`: extract, read, compute, memo, tier, award |
| Template | [`templates/procurement-operations`](templates/procurement-operations/README.md) | workflow `supplier-onboarding`: extract, read, measure, review, create the record under approval; workflow `purchase-request`: qualify a request and route it, nothing opened; needs the connector `purchasing` |
| Server | [`servers/purchasing.yaml`](servers/purchasing.yaml), source [`server/`](server/PROFILE.md) | the MCP server `purchasing`: eleven operations of a fixed profile, served by Odoo 19 and 20 and by a seeded fake |
| Procedure | [`procedures/connector.mcp.purchasing.md`](procedures/connector.mcp.purchasing.md) | how the server is connected to Odoo: a dedicated user, the least rights, an API key |
| Evals | [`evals/offer-reader.yaml`](evals/offer-reader.yaml), [`evals/sourcing-analyst.yaml`](evals/sourcing-analyst.yaml), [`evals/supplier-file-checker.yaml`](evals/supplier-file-checker.yaml), [`evals/request-qualifier.yaml`](evals/request-qualifier.yaml) | instruction checks, and live cases each, with invented suppliers and amounts |

## What version 0.2.0 adds: the server `purchasing`

An MCP server the platform runs and governs, named after what it does and not after a vendor: five reads
(`search_suppliers`, `get_supplier`, `list_framework_agreements`, `list_contracts`, `get_contract`) and six
writes (`create_supplier`, `update_supplier`, `archive_supplier`, `add_contract_note`,
`create_purchase_request`, `cancel_purchase_request`). Every write waits for a person, takes the platform's
`action_key` (the same key twice writes once), and three come with a compensation. No operation takes or
returns a bank detail. The profile is fixed in [`server/PROFILE.md`](server/PROFILE.md).

- **Proves**: the profile end to end on a seeded, in-memory fake, in three scenes; that the descriptor and the
  served tools agree; the idempotency, compensation and no-bank-detail rules, by tests that fail without them; and
  the **Odoo backend** (Odoo 19 and 20, through the vendor's JSON-2 API and an API key) on hand-written exchanges:
  every mapped operation, its error paths and its paging, typed filters, the replay of a write after a restart, no
  bank detail on any request, no key or traceback in any error.
- **Does not prove**: anything against a running Odoo: the live proof is the core's S84-14 on a container. What
  Odoo cannot say is *not supported* and written in [`server/PROFILE.md`](server/PROFILE.md): an agreement has no
  category, a contract has no value and is the same record as an agreement, a unit of measure is written in a
  line's name, and on Odoo 20 a supplier's registration number is not supported. Odoo 18 and older are not served.
  The idempotence that survives a restart does not cover two identical calls racing on two replicas.

## What version 0.2.0 adds: supplier onboarding

The agent `supplier-file-checker` and the template `procurement-operations`, whose workflow
`supplier-onboarding` is the plugin's first governed write: a supplier record created in the purchasing system
after a person of the group `supplier-data` reviewed a file whose completeness a job measured, and after two
people of that group approved the creation. The agent copies and dates (it never says a document is valid or
expired, and never copies a bank detail); the job `supplier-check` measures what is missing, expired,
different from the request or already held. **No bank detail is handled.**

- **Proves**: the workflow's structure (its only action, its approval, its measured gates, its schema), by
  tests that fail without each guarantee.
- **Does not prove**: a run of the workflow, or what a model writes (the agent's live evals are not played
  by CI); that a document is genuine.

## What version 0.2.0 adds: purchase requests, qualified

The agent `request-qualifier` and the workflow `purchase-request` (template `procurement-operations`): an
employee's request is read against the framework agreements in force and the organisation's own policy (the
knowledge base `purchasing-policy`, shipped empty), one route is proposed, and a buyer chooses the route,
attested. The workflow opens nothing in the purchasing system. The agent must always look at the agreements
(a gate reads the ledger), writes only numbers the requester typed, and asks when the amount is missing.

- **Proves**: the workflow's structure (no action, the four gates, the route values, the attested review), and
  the core's real gates on outputs an agent could write, by tests that fail without each guarantee.
- **Does not prove**: what a model writes, that a route is the right one, or a run (the live evals are not played
  by CI). An agreement's id is read as a reference, not an amount (`identifiers: skip`, see the template README).

## What version 0.1.0 holds

The two agents, their two skills, their evals and the template `procurement-sourcing` that wires them
(extraction, the offer-reader, a computing step, the sourcing-analyst, a tier, a person's attested
decision). The job image `procurement` that extracts and computes, and that the template's job steps name,
comes in a later change. The `core` floor `0.28.0` is **provisional**: it
stands for the first core release that carries the reserved input `attachments` (core ADR 0197) and is to
be corrected to that release's version when the plugin is published.

Both agents declare `memories: []`, no catalogue tool, no extension, no web and no MCP server: supplier
documents are confidential and untrusted, and an offer must not reach another consultation.

## `offer-reader`

Given `offers_text` (the extracted Markdown of each handed file, amounts already in decimal-point
notation), it writes `offers_structured`: per supplier its files, its lines, its stated total, payment
terms, delivery, validity date and exclusions, plus the files it could not assign.

- **Guarantees**: it copies a number as the text writes it, computes nothing, leaves a missing value
  `null`, places every file exactly once, and reports an instruction found in an offer as a quoted
  remark in that supplier's `exclusions` instead of following it. A later gate refuses any number of its
  output that the extracted text does not hold.
- **Does not guarantee**: that the offer is right (a supplier's own arithmetic mistake is copied
  faithfully; the deterministic step finds it), that the right supplier was named for a file, or that a
  copied number is the one a person would read as the price.

## `sourcing-analyst`

Given `comparison` and `award_basis` (computed by a deterministic step), `offers_text` and, when a person
sent the memo back, `human_feedback`, it writes `comparison_memo_markdown` with seven sections: Summary,
Files read, Price comparison, Differences and exclusions, Checks that failed, Questions for the
suppliers, What this memo does not cover.

- **Guarantees**: every number comes from `comparison` or is quoted from the offers; every number of
  `award_basis`, each mismatch included, is written; it ranks by the measured total, lists what differs,
  answers each point of a person's feedback, writes in the language of the work item, and **does not
  recommend a supplier**: the award is a person's decision.
- **Does not guarantee**: that the lowest total deserves the award, anything about quality, references or
  risk, or that an instruction hidden in an offer cannot bend the wording of the memo.

## What a reader must know (ADR 0198)

- **No OCR.** A scanned offer is not read; it stops at a person.
- **No injection detector.** The agents are told to treat an offer as data and to report an instruction,
  but the protection is mechanical: no write outside the platform, number gates that bound what the memo
  can claim, no credential and no network in the agents, and a person who reads the memo.
- **Nothing is written outside the platform without a person.** The template of 0.1.0 has no governed action. The
  server of 0.2.0 has writes to Odoo, each held for a person and re-authenticated; the first version of the
  backend has had no live run.

## Checks

```sh
tranibor plugin validate plugins/procurement
tranibor plugin pack plugins/procurement -o procurement-0.2.0.tar.gz   # same bytes every time
```

The server's own checks, in `server/` (the `servers` job): `uv sync --all-extras --dev`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy --strict src`, `uv run pytest -q`.
