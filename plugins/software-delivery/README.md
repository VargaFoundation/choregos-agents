# Software delivery plugin

The agents of a governed software delivery, from triage to the production go or no-go. They are the core's built-in catalogue entries (ADR 0040), unchanged; the core's `github-software-delivery` and `software-delivery-governed` templates name them.

| Agent | Catalogue version | What it does |
|:--|--:|:--|
| [`architect`](agents/architect.yaml) | 2 | Writes one architecture decision record in MADR 4 format, citing the enterprise framework when a knowledge base holds it; once approved, marks it accepted. |
| [`ci-fixer`](agents/ci-fixer.yaml) | 1 | Makes a red CI green with the smallest fix of the cause; reports flaky tests, never disables one silently. |
| [`compliance`](agents/compliance.yaml) | 1 | Licences of dependencies, personal-data impact of a change, third-party terms — a review for the people who decide, never legal advice. |
| [`developer`](agents/developer.yaml) | 1 | Implements the specification inside the allowed paths, test first, small conventional commits. |
| [`devops`](agents/devops.yaml) | 1 | Release readiness and a rollout plan before the train; after it, reads logs and metrics through read-only connectors. Production stays behind the train. |
| [`docs-writer`](agents/docs-writer.yaml) | 1 | Updates the user documentation, the changelog and the API reference from the branch's diff, and checks that every visible change is documented. |
| [`planner`](agents/planner.yaml) | 1 | Writes the implementation plan — ordered steps, files, the test that proves each step, rollback. |
| [`prod-verifier`](agents/prod-verifier.yaml) | 2 | After the release train has deployed, checks production read-only and gives a go or no-go. |
| [`qa`](agents/qa.yaml) | 1 | A test plan from the specification, the edge cases nobody wrote, and mutation testing on the changed code — a test that cannot fail proves nothing. |
| [`release-notes-writer`](agents/release-notes-writer.yaml) | 1 | Writes user-facing release notes from the specification and the diff; changes no file. |
| [`researcher`](agents/researcher.yaml) | 1 | Researches a question in the code, the existing decisions and memory; lists options with evidence; changes nothing. |
| [`review-responder`](agents/review-responder.yaml) | 1 | Answers every review remark — fixes it or says why not — and refactors nothing else. |
| [`reviewer`](agents/reviewer.yaml) | 1 | Reviews the branch with fresh eyes — correctness, security, performance, readability, tests — and gives a verdict. |
| [`secops`](agents/secops.yaml) | 1 | Supply chain and runtime posture — dependency CVEs, SBOM, secrets, container and IaC scans — run with the image's scanners and interpreted, as a SARIF report. |
| [`security-reviewer`](agents/security-reviewer.yaml) | 1 | A security-only review — injection, authorization, secrets, dependencies, data exposure — with a verdict. |
| [`spec-writer`](agents/spec-writer.yaml) | 2 | Turns a work item into a testable specification and the minimal list of paths the change may touch. |
| [`tester`](agents/tester.yaml) | 1 | Runs the full checks, writes the missing tests for each acceptance criterion, never fixes application code. |
| [`triager`](agents/triager.yaml) | 1 | Sizes and qualifies an incoming work item; asks at most three questions; changes no file. |
| [`ux-designer`](agents/ux-designer.yaml) | 1 | Reviews user-facing changes against the organisation's design system (a skill or a knowledge base) and checks accessibility, with axe when the project can run it. |

The skill [`madr-4`](skills/madr-4/SKILL.md) (MADR 4.0.0 decision records, carried by `architect` and `reviewer`) ships with MADR's licence (`MIT OR CC0-1.0`, the MIT text in `LICENSE`) and its `PROVENANCE.json`. The gate rates it high risk (it links to adr.github.io), so installing this plugin waits for a second administrator (ADR 0050).

Version 1.0.0, core `>=0.21.0,<1.0.0`. The instructions are the core's, byte for byte: the plugin's files
do not hold the work-item context, which the core prepends when it installs an internal agent. See
[MIGRATION-FROM-CORE.md](../../MIGRATION-FROM-CORE.md).

```sh
choregos plugin validate plugins/software-delivery
choregos plugin pack plugins/software-delivery -o software-delivery-1.0.0.tar.gz   # same bytes every time
```
