---
name: acceptance-criteria
description: How to write testable acceptance criteria in Given, When, Then, and a minimal list of allowed paths. Use it when writing a specification, and when mapping a criterion to its test.
---

<!-- SPDX-License-Identifier: Apache-2.0 -->

# Acceptance criteria that a test can prove

## Form

```
Given <a state that can be set up in a test>
When  <one action by a user, a client or the clock>
Then  <an outcome that can be asserted: a response, a stored value, an event, a message>
```

- One behaviour per criterion; several `Then` lines only if they are one outcome.
- Concrete values: "Given an invoice of 100.00 EUR already refunded for 60.00", not "a partially refunded invoice".
- State the **error** criteria next to the happy path: invalid input, not allowed, not found, dependency down.
- Observable only: not "the code uses a cache", but "the second call answers within the cache's limit".
- No criterion you cannot test; if it needs a decision, ask (`ask_human`) instead of inventing it.

## Out of scope

Name what a reader would assume is included and is not. A criterion that is out of scope is better written
as an out-of-scope line than left out.

## Test plan

For each criterion: the level (unit, integration, end to end), the file where the test goes, and the edge
cases (boundaries, empty, concurrency, errors, permissions).

## Allowed paths

The **minimal** list of paths the change may touch, each justified in a few words: the module, its tests,
and the documentation that a visible change needs. Prefer a directory of the module over a wildcard of the
repository. A path missing from the list stops the developer: when in doubt about documentation or a
migration, include it.
