---
name: release-notes
description: How to write release notes and changelog entries a user can read. Use it when writing the release notes of a change, a changelog entry, or the summary of what a release changes.
---

<!-- SPDX-License-Identifier: Apache-2.0 -->

# Release notes and changelog

## Release notes (for users and operators)

Group under **New**, **Fixed**, **Technical** (and **Breaking** first when something is).

- One line per change, in the imperative or plain present, in what the reader sees: "Add credit notes to
  the invoice list", not "Added CreditNoteRepository".
- No internal names, ticket jargon or class names; the ticket key goes at the end in brackets when the
  project uses one: `(FIN-42)`.
- **Breaking**: say what the reader must do, in one sentence, with the old and the new form.
- **Data migrations**: say they exist, whether they run on their own, how long they may take, whether
  they can be rolled back.
- **Behind a feature flag**: say the change is inactive until the flag is on, and the flag's name.
- **Technical**: dependency bumps, configuration keys added or renamed, anything an operator must set.
- A change that has no visible effect is not a release note; it may be a Technical line.

## Changelog entry (in the repository)

- Follow the format the file already uses (Keep a Changelog sections `Added`, `Changed`, `Deprecated`,
  `Removed`, `Fixed`, `Security`, under `Unreleased`). Do not invent a second format.
- Add the entry under `Unreleased`; never edit a released section.
- Pre-1.0, a breaking change bumps the minor version; say so only if the project's file does.

## Check before you finish

Every visible change of the diff has a line; nothing is claimed that the diff does not do; every number,
flag and command quoted exists in the change.
