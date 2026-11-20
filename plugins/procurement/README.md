# Procurement plugin

Compare supplier offers with agents that are never trusted with arithmetic. This is the content half of
the plugin of the Tranibor core's ADR 0198 (`procurement is a plugin whose code half is a job image`).

| Part | Path | What it is |
|:--|:--|:--|
| Manifest | [`plugin.yaml`](plugin.yaml) | `procurement` 0.1.0, core `>=0.28.0,<1.0.0` (provisional floor, below) |
| Agent | [`agents/offer-reader.yaml`](agents/offer-reader.yaml) | catalogue role `structure`: copies the offers into `offers_structured` |
| Agent | [`agents/sourcing-analyst.yaml`](agents/sourcing-analyst.yaml) | catalogue role `compare`: writes `comparison_memo_markdown` |
| Skills | [`skills/reading-supplier-offers`](skills/reading-supplier-offers/SKILL.md), [`skills/writing-comparison-memos`](skills/writing-comparison-memos/SKILL.md) | the output contract and rules of each agent |
| Evals | [`evals/offer-reader.yaml`](evals/offer-reader.yaml), [`evals/sourcing-analyst.yaml`](evals/sourcing-analyst.yaml) | instruction checks, and six live cases each, with invented suppliers and amounts |

## What version 0.1.0 holds

The two agents, their two skills and their evals. Nothing runs them yet: the template that wires them
(extraction, the offer-reader, a computing step, the sourcing-analyst, a person's decision) and the job
image that extracts and computes come in later changes. The `core` floor `0.28.0` is **provisional**: it
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
- **Nothing is written outside the platform.** Version 0.1.0 has no governed action and no server.

## Checks

```sh
tranibor plugin validate plugins/procurement
tranibor plugin pack plugins/procurement -o procurement-0.1.0.tar.gz   # same bytes every time
```
