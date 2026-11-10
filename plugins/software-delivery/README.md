# Software delivery plugin

The agents of a governed software delivery, from triage to the production go or no-go. They are the core's built-in catalogue entries (ADR 0040), unchanged; the core's `github-software-delivery` and `software-delivery-governed` templates name them.

| Agent | Catalogue version | What it does |
|:--|--:|:--|
| [`architect`](agents/architect.yaml) | 3 | Writes one architecture decision record in MADR 4 format, citing the enterprise framework when a knowledge base holds it; once approved, marks it accepted. |
| [`ci-fixer`](agents/ci-fixer.yaml) | 2 | Makes a red CI green with the smallest fix of the cause; reports flaky tests, never disables one silently. |
| [`compliance`](agents/compliance.yaml) | 2 | Licences of dependencies, personal-data impact of a change, third-party terms — a review for the people who decide, never legal advice. |
| [`developer`](agents/developer.yaml) | 2 | Implements the specification inside the allowed paths, test first, small conventional commits. |
| [`devops`](agents/devops.yaml) | 2 | Release readiness and a rollout plan before the train; after it, reads logs and metrics through read-only connectors. Production stays behind the train. |
| [`docs-writer`](agents/docs-writer.yaml) | 2 | Updates the user documentation, the changelog and the API reference from the branch's diff, and checks that every visible change is documented. |
| [`planner`](agents/planner.yaml) | 2 | Writes the implementation plan — ordered steps, files, the test that proves each step, rollback. |
| [`prod-verifier`](agents/prod-verifier.yaml) | 3 | After the release train has deployed, checks production read-only and gives a go or no-go. |
| [`qa`](agents/qa.yaml) | 2 | A test plan from the specification, the edge cases nobody wrote, and mutation testing on the changed code — a test that cannot fail proves nothing. |
| [`release-notes-writer`](agents/release-notes-writer.yaml) | 2 | Writes user-facing release notes from the specification and the diff; changes no file. |
| [`researcher`](agents/researcher.yaml) | 3 | Researches a question in the code, the existing decisions and memory; lists options with evidence; changes nothing. |
| [`review-responder`](agents/review-responder.yaml) | 2 | Answers every review remark — fixes it or says why not — and refactors nothing else. |
| [`reviewer`](agents/reviewer.yaml) | 2 | Reviews the branch with fresh eyes — correctness, security, performance, readability, tests — and gives a verdict. |
| [`secops`](agents/secops.yaml) | 2 | Supply chain and runtime posture — dependency CVEs, SBOM, secrets, container and IaC scans — run with the image's scanners and interpreted, as a SARIF report. |
| [`security-reviewer`](agents/security-reviewer.yaml) | 2 | A security-only review — injection, authorization, secrets, dependencies, data exposure — with a verdict. |
| [`spec-writer`](agents/spec-writer.yaml) | 3 | Turns a work item into a testable specification and the minimal list of paths the change may touch. |
| [`tester`](agents/tester.yaml) | 2 | Runs the full checks, writes the missing tests for each acceptance criterion, never fixes application code. |
| [`triager`](agents/triager.yaml) | 2 | Sizes and qualifies an incoming work item; asks at most three questions; changes no file. |
| [`ux-designer`](agents/ux-designer.yaml) | 2 | Reviews user-facing changes against the organisation's design system (a skill or a knowledge base) and checks accessibility, with axe when the project can run it. |

The skill [`madr-4`](skills/madr-4/SKILL.md) (MADR 4.0.0 decision records, carried by `architect` and `reviewer`) ships with MADR's licence (`MIT OR CC0-1.0`, the MIT text in `LICENSE`) and its `PROVENANCE.json`. The gate rates it high risk (it links to adr.github.io), so installing this plugin waits for a second administrator (ADR 0050).

Version 1.1.1, core `>=0.25.1,<1.0.0`. The instructions are the core's, byte for byte: the plugin's files
do not hold the work-item context, which the core prepends when it installs an internal agent. See
[MIGRATION-FROM-CORE.md](../../MIGRATION-FROM-CORE.md).


## Declared components (1.1.1)

Every agent declares the five components of ADR 0127 (the core's S60-04), so that a version no longer
inherits any of them from its project. Each agent's catalogue version is one higher than in 1.0.0 (`researcher` two: 1.1.1 restores the
web that 1.1.0 closed), and no instruction changed. `tools.web` and `tools.extensions` are declared even when empty: a list that is present
closes its source. The project and the deployment still only tighten what is named here.

| Agent | Model | `catalogue` | `extensions` | `web` | `memories` | Why |
|:--|:--|:--|:--|:--|:--|:--|
| `architect` | `profile:by_size` | none | none | none | `project/shared` write | Writes a record from the research handed to it and the repository; no tool source of its own. |
| `ci-fixer` | `profile:by_size` | none | none | none | `project/shared` write | Reads CI logs through the platform's own tools (`get_ci_logs`, the frame), edits code in the workspace. |
| `compliance` | `profile:by_size` | none | none | none | `project/shared` write | Runs `trivy` in the workspace, reads knowledge bases (declared by the version, not here). |
| `developer` | `profile:by_size` | none | none | none | `project/shared` write | Writes code and runs the repository's checks in its workspace; no instruction names an outside tool. |
| `docs-writer` | `profile:by_size` | none | none | none | `project/shared` write | Edits documentation files in its workspace. |
| `planner` | `profile:by_size` | none | none | none | `project/shared` write | Reads the specification; writes a plan. |
| `qa` | `profile:by_size` | none | none | none | `project/shared` write | Runs `mutmut` or `stryker` from the runner image, edits tests. |
| `release-notes-writer` | `profile:by_size` | none | none | none | `project/shared` write | Reads the specification and the diff. |
| `researcher` | `profile:by_size` | none | none | `fetch`, `search` | `project/shared` write | Reads the repository, decision records and the project's memory (`search_memory`), and the web its project's network allows: its eval E5 (`research-injected-page`) searches and fetches pages. 1.1.0 declared no web, which closed it; 1.1.1 restores it. No catalogue tool is named. |
| `review-responder` | `profile:by_size` | none | none | none | `project/shared` write | Fixes the remarks of a review in its workspace. |
| `reviewer` | `profile:by_size` | none | none | none | `project/shared` write | Reads the diff. |
| `secops` | `profile:by_size` | none | none | none | `project/shared` write | Runs `trivy` and `gitleaks` from the runner image. |
| `security-reviewer` | `profile:by_size` | none | none | none | `project/shared` write | Reads the diff. |
| `spec-writer` | `profile:by_size` | none | none | none | `project/shared` write | Reads the code, asks people with `ask_human` (the frame). |
| `tester` | `profile:by_size` | none | none | none | `project/shared` write | Runs the checks and edits tests in its workspace. |
| `triager` | `profile:by_size` | none | none | none | `project/shared` write | Uses `search_memory` for duplicates: the project's memory, as it always did. |
| `ux-designer` | `profile:by_size` | none | none | none | `project/shared` write | Runs axe in the workspace (a shell command, not a web tool); reads the design system as a skill or a knowledge base. |
| `devops` | `profile:by_size` | `group:observability` | none | none | `project/shared` write | Reads logs and metrics after the train: the catalogue's observability tools (the ones that decide `production_indicators`). Production stays behind the train, and the catalogue never offers a tool that may write. |
| `prod-verifier` | `profile:by_size` | `group:observability` | none | none | `project/shared` write | Reads the service indicators when the project has a source of them; `smoke_*` facts are measured by the platform, not called by the agent. |

`profile:by_size` is what these agents ran with unless a workflow actor overrode it; it is an explicit choice
here, not a new one. The memory is what the legacy `memory: project` meant (`effective_bindings`): the run's
lessons go to the project's shared memory. A later version may narrow it.

```sh
choregos plugin validate plugins/software-delivery
choregos plugin pack plugins/software-delivery -o software-delivery-1.1.1.tar.gz   # same bytes every time
```
