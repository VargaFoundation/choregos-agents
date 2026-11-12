---
name: work-item-sizing
description: A rubric to estimate the size (S, M, L, XL) and the risk (low, medium, high) of a work item, and to decide whether to ask. Use it when triaging a work item or when a specification must state its size and risk.
---

<!-- SPDX-License-Identifier: Apache-2.0 -->

# Size and risk of a work item

## Size (agent time, including tests and documentation)

| Size | Typical shape |
|:--|:--|
| `S` | half a day or less: one module, no new concept, a test or two, no migration |
| `M` | one to two days: a few modules, one new concept, a migration of a small table, one integration |
| `L` | three to five days: a new feature across layers, a public contract change, several migrations |
| `XL` | more: touches many modules or teams, a new subsystem, an unknown to research first. Say it should be split |

Raise the size by one step for each of: unfamiliar code, no tests around the area, a change to a public API,
a data migration, a dependency upgrade across a major version.

## Risk

| Risk | When |
|:--|:--|
| `high` | money, personal data, authentication or authorisation, an irreversible migration, deletion of data, a change many callers depend on, production configuration |
| `medium` | a visible behaviour change, a schema change that can be rolled back, a new external dependency, concurrency |
| `low` | internal, covered by tests, reversible by reverting the commit |

The highest of several signals wins. A small change can be high risk.

## Duplicates and questions

- A duplicate is the same **outcome** wanted, not the same words: compare the acceptance of the other
  item; give its key.
- Ask only what changes the size, the risk or what to build, at most three questions, each answerable in
  a sentence, each with the default you will assume if nobody answers.
- Do not ask what the repository answers: read it first.
