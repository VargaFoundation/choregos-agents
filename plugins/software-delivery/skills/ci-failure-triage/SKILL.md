---
name: ci-failure-triage
description: How to find the cause of a red CI, tell a real failure from a flaky one, and make the smallest fix. Use it when a pipeline fails, a test fails only in CI, or a failure may be flaky.
---

<!-- SPDX-License-Identifier: Apache-2.0 -->

# From a red CI to the smallest fix

1. **Find the first failure**, not the last: scroll to the first error in the log, the rest are usually consequences.
2. **Name its class**:

| Class | Signs | Usual fix |
|:--|:--|:--|
| Real regression | fails the same way on every run; the diff touches the code under test | fix the code or the test the diff broke |
| Environment | works locally; differs in version, locale, timezone, path, missing env var, network | pin or declare what the CI needs; do not hide it |
| Dependency | install or resolve step fails; lockfile out of step with the manifest | regenerate the lockfile with the project's tool |
| Build or lint | formatter, linter or type checker; a generated file out of date | run the project's formatter or generator and commit the result |
| Flaky | passes on re-run; depends on time, ordering, concurrency, an external service | report it (`report_finding`, type `flaky-test`); never delete or skip it silently |
| Infrastructure | runner out of disk or memory, registry or network timeout, quota | not a code fix: finish `blocked` with the proof |

3. **Reproduce** with the same command the CI runs (find it in the pipeline file), not an approximation.
4. **Smallest change** that turns it green: one cause, one fix, in the allowed paths. No refactor, no
   version bump unrelated to the failure, no loosening of a check.
5. **Re-run** the failing command and the whole suite; say what you ran.

## Never

- Disable, skip, `xfail` or delete a test, lower a threshold, or add a retry to get green without saying so.
- Mark a failure flaky because it passed once: show two runs that disagree on the same commit.
- Fix an unrelated failure on the way: report it.
