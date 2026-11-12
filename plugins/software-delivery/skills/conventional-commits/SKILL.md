---
name: conventional-commits
description: The Conventional Commits format for small, reviewable commits. Use it every time you write a commit message, split work into commits, or reword one.
---

<!-- SPDX-License-Identifier: Apache-2.0 -->

# Conventional Commits

```
<type>(<scope>): <summary in the imperative, under 72 characters, no final period>

<body: why, not what; wrap at 72; optional>

<footer: BREAKING CHANGE: ..., Refs: TICKET-123; optional>
```

## Types

| Type | Use for |
|:--|:--|
| `feat` | a behaviour a user or API client can newly do |
| `fix` | a defect corrected |
| `refactor` | a change of structure with no change of behaviour |
| `perf` | a measured speed or memory gain |
| `test` | tests only |
| `docs` | documentation only |
| `build`, `ci` | build system, dependencies, pipelines |
| `chore` | anything else that touches no behaviour |

## Rules

- **One intent per commit.** A refactor and a behaviour change are two commits; so are a fix and the
  unrelated thing you noticed (report that one, do not fix it).
- Every commit leaves the branch green: the test that proves the change is in the same commit as the change.
- The scope is the module, package or area the repository already names (`fix(orders): ...`); look at
  `git log --oneline` for the vocabulary in use, and follow it.
- A breaking change has a `!` after the scope (`feat(api)!: ...`) **and** a `BREAKING CHANGE:` footer
  that says what the caller must do.
- The summary says what the commit does (`add credit notes`), not what you did (`added`, `changed stuff`).
- Reference the work item in the footer when the project's tracker requires it.
- Never commit a secret, a generated file the repository ignores, or a skipped hook: if a hook fails, fix the cause.
