---
name: code-review
description: A review checklist and the way to word remarks so the author can act on each. Use it when reviewing a branch diff, and when answering a review remark by remark.
---

<!-- SPDX-License-Identifier: Apache-2.0 -->

# Reviewing a diff

Review the **diff** against the specification, not the whole repository. Read the tests first: they say
what the author believes the change does.

## Checklist, in this order

1. **Correctness**: does each acceptance criterion hold? Boundaries, empty values, error paths, ordering,
   idempotency of a retry, a partially failed write, a changed public contract (field, status code, signature).
2. **Security**: input crossing a trust boundary, authorisation checked on the server side, secrets and
   personal data in code or logs, new dependencies (see the `security-review` skill when the diff touches
   authentication, data access or a dependency).
3. **Data and compatibility**: a migration that cannot be rolled back, a column dropped before the code stops
   reading it, a renamed field with no alias, a changed default.
4. **Performance**: a query or call inside a loop (N+1), unbounded reads, work repeated on a hot path,
   a missing index for a new filter.
5. **Readability**: names that say what a thing is, a function that does one thing, comments that say *why*,
   no dead code, no commented-out code.
6. **Tests**: they fail if the code is broken; one per criterion; no test deleted or weakened to get green.
7. **Scope**: nothing changed outside what the work item asks.

## Wording a remark

`path:line — what is wrong — what to do`, with one class:

- **blocking**: a defect, a vulnerability, a broken contract, a missing test of a criterion. The verdict is
  `request_changes` as soon as one exists.
- **to fix**: should change before merge, does not break anything today.
- **suggestion**: take it or leave it; never a reason to hold the merge.

State the **evidence** (the input that breaks it, the line it contradicts), not a taste. Do not rewrite the
author's design in a remark; propose the smallest change. Say what is good in one line when it is.

## Answering a review

For each remark: fixed (say where) or declined (one sentence of reason). Every blocking remark gets an
answer. A fix may not widen the change: a refactor on the way needs its own review.
