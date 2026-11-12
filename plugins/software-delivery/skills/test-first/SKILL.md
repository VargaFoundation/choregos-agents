---
name: test-first
description: How to work test first and how to judge that a test proves something. Use it before writing a change (red, green, refactor), when adding the missing test of an acceptance criterion, and when checking that a test would fail if the code were broken.
---

<!-- SPDX-License-Identifier: Apache-2.0 -->

# Test first

## The loop

1. **Red**: write the smallest test that states the new behaviour, run it, and watch it fail **for the
   reason you expect** (the assertion, not an import error or a typo). A test you never saw fail proves nothing.
2. **Green**: write the least code that makes it pass. Run that test, then the whole suite.
3. **Refactor**: only with the suite green; one intent per commit.

## What a good test is

- It names the behaviour, not the function: `test_refund_over_the_paid_amount_is_refused`.
- One reason to fail. Arrange, act, assert, in that order, with no logic (no loop, no `if`) in the test.
- It asserts the **outcome a caller sees** (return value, stored state, emitted event, HTTP status and body),
  not how the code got there.
- It fails if the code is broken: after writing it, mentally (or really) invert a condition, drop a
  boundary, return a constant; if the test still passes, strengthen it.

## Where the cases come from

| Source | Cases |
|:--|:--|
| each acceptance criterion | at least one test, named after it |
| boundaries | zero, one, maximum, one past the maximum, off-by-one |
| absence | empty, null, missing field, empty collection |
| time and order | two requests at once, a retry, an out-of-order event, a timezone, a leap day |
| failure | timeout, invalid input, a dependency that is down, a partial write |
| permission | the caller who must be refused, not only the one who is allowed |

## Rules

- Use the repository's own framework, fixtures, markers and naming; look at the nearest existing test first.
- No network, no wall clock, no randomness without a fake or a seed: a flaky test is a defect, not a retry.
- Never weaken or delete an existing test to get green. If it is wrong, say why in the commit.
- Never report a count (`tests_run`, `tests_passed`) you did not read from the runner's output.
