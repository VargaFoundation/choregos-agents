# procurement-sourcing — compare supplier offers on measured numbers

One workflow, `offer-comparison`, for a buyer who hands the offers of a consultation as files and wants
a memo whose every number was computed by a job, and an award that a person attests.

The work item takes two to twenty files (`pdf`, `docx`, `xlsx`, `csv`) and two fields: `currency` (three
upper-case letters, required) and `number_format` (`auto`, `decimal_comma` or `decimal_point`).

| Step | Who | What happens | Gates | On failure |
|:--|:--|:--|:--|:--|
| `t-extract` | job `extract` | converts each file to text, amounts in decimal-point notation | `outputs_present`; measured `files_read` >= 2, `files_unreadable` = 0, `files_truncated` = 0 | a person (`needs_human`) |
| `t-read` | agent `offer-reader` | copies what each offer says into `offers_structured`, a currency per supplier | `output_schema` (the job image's schema); `numbers_measured` against `offers_text` | back to `extracted` twice, then a person |
| `t-compute` | job `compare` | totals, ranking, spread, every arithmetic mismatch of the offers | `outputs_present`; measured `suppliers_count` >= 2, `files_unassigned` = 0, `currencies` <= 1 | a person |
| `t-memo` | agent `sourcing-analyst` | writes `comparison_memo_markdown` | `output_sections` (the seven sections, in order); `numbers_measured` against `comparison` and `offers_text`, `must_quote` of `award_basis` | back to `computed` twice, then a person |
| `t-tier` | job `tier` | `approval_tier` and `highest_total`, against `--committee-from 50000` | `outputs_in` on `approval_tier` | a person |
| routing | the platform | `committee` goes to the committee, anything else to the buyer | `when` on `approval_tier` | |
| award | buyer (48 h) or committee (72 h) | awards with a supplier and a rationale, attested | the attestation | |

Each deciding person has three exits: **award** (`awarded`), **send back** with feedback (the memo is
rewritten on `computed`, and the analyst answers each point), or **close with no award** and a reason
(`not_awarded`). A work item that needs a person waits in `needs_human`, resumes when answered or ends in
`abandoned` after 168 hours.

## What the workflow guarantees

- No file is read as an instruction: the runner introduces them as data, and the agents hold no web, no
  catalogue tool, no memory and no credential. The policy is `preset:regulated` (closed sandbox).
- Every total, rank and spread comes from a job. A number in the structured offers that the extracted
  text does not hold is refused (`t-read`); a number in the memo that `comparison` and the offers do not
  hold is refused (`t-memo`); the memo must write every number of `award_basis`, each mismatch included.
- A comparison runs only on at least two suppliers, every file placed, a single currency and no
  unreadable or truncated file. Otherwise it stops at a person, not at a retry.
- The memo has its seven sections in order. It ranks by price and recommends nobody.
- The committee threshold is an argument of the job in the workflow file, pinned to the work item when it
  starts; no field can lower it.
- The award is a person's, with a supplier, a rationale and the attestation "I award this consultation on
  the basis of the files and the memo I read."
- Nothing is written outside the platform.

## What it does not guarantee (ADR 0202)

- **No OCR.** A scanned offer is not read; it counts in `files_unreadable` and stops at a person.
- **No injection detector.** The protection is mechanical (nothing written outside the platform, number
  gates, no credential and no network in the agents, a person who reads the memo). An instruction hidden
  in an offer can still bend the wording of a memo.
- **Measured does not mean true.** A supplier's own mistake is copied faithfully; arithmetic ones are
  found and listed, the rest is for the person who awards. No currency conversion: two currencies stop
  the comparison.

The `tranibor-procurement` commands run in the job image `procurement`, which the operator maps. Validated
by `tranibor plugin validate` and the core's workflow parser; the dropping of `--committee-from` from the
`tier` command is not caught here, it is the job of the template test of the plugin's CI.
