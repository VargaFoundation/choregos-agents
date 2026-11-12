---
name: safe-rollout
description: How to plan a change so it can be released and rolled back safely - migrations in expand, migrate, contract order, feature flags, a rollout plan with signals and rollback triggers. Use it when planning an implementation that touches data, and when assessing release readiness.
---

<!-- SPDX-License-Identifier: Apache-2.0 -->

# Safe rollout

## Data changes: expand, migrate, contract

Never change a schema and its readers in one step. Three releases at most:

1. **Expand**: add the new column, table or field (nullable or defaulted); the old code still works.
2. **Migrate**: write to both, backfill the old rows in batches, switch readers to the new shape.
3. **Contract**: once nothing reads the old shape and a release has run on the new one, drop it.

A migration is reversible, idempotent, and runs once (as a job before the new version, not at every start
of every replica). Say how long it takes on production-sized data and whether it locks.

## Behind a flag

A behaviour that a release cannot take back (a new payment path, a deletion) ships off, behind a named
flag, and is switched on separately. Say the flag's name, default and owner.

## Release readiness checklist

- Migrations: order, reversibility, duration, lock.
- Configuration and secrets the release needs, and where each lives (never in git).
- Dependencies on other services and the order they must deploy in.
- Anything done by hand, and who owns it. A manual step nobody owns blocks the release.

## The rollout plan

| Part | Content |
|:--|:--|
| Order | the environments, in order, and what is verified at each |
| Success signals | the metrics or checks to watch, **with thresholds** (error rate, latency, a business figure) and how long |
| Rollback trigger | the exact condition that means go back |
| Rollback steps | the exact steps, and what they do **not** undo (data written meanwhile) |

## Verifying a deployed change

Compare the signals before and after. A figure you could not observe is **not observed**: never invent one,
and never read the absence of a figure as proof that all is well.
