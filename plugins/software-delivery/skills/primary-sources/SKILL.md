---
name: primary-sources
description: How to research with primary sources and cite them - what to fetch, how to judge a source, how to record what was read. Use it when a decision or an option depends on an external standard, a product's behaviour, a licence or a version.
---

<!-- SPDX-License-Identifier: Apache-2.0 -->

# Researching with primary sources

## Order of trust

1. The repository itself: code (`path:line`), decision records, configuration, lockfiles.
2. The **primary** source: the standard or RFC, the vendor's documentation for the **version in use**, the
   project's own source, release notes, the licence text.
3. A maintainer's statement (issue, mailing list), dated.
4. Everything else (blog posts, forums, summaries): a lead to verify, never the evidence.

## Method

- Fetch the page; do not rely on a search snippet. Prefer the version-specific documentation page.
- Record **what you read**: the URL, the version or date of the page, and the sentence that supports the
  claim. A claim without that record is removed.
- Two sources that disagree: say so, give both, say which is primary.
- Check the version: the behaviour of the version in the lockfile, not the latest.
- A page you could not fetch (blocked host, error) is **not read**: say so, and mark the dependent claim
  unverified.

## What to cite

- An option's cost, limit or behaviour that you took from outside the repository.
- A standard a decision follows or departs from.
- A licence, a support status or an end-of-life date.

Cite as `[title](URL), version or date`. Never paste long passages: quote a line and link.
