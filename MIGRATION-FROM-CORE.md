# Migration from the core's built-in catalogue

The core ships a built-in agent catalogue (ADR 0040) in
`packages/core/src/choregos_core/catalogue_d_agents/`. The plugins below carry all of it, unchanged, so
that the core can install them instead (S43-09). This file is the mapping that migration reads.

**Source**: VargaFoundation/choregos at `3156d12eabb7dc11fd54d821604cb1efb0033b04` (core 0.20.6 + S43):
22 internal agents (`agents/*.yaml`), 6 external MCP clients (`clients/*.yaml`), 25 eval cases
(`eval_cases/`), 1 skill (`skills/madr-4`).

**Plugins**: `software-delivery`, `netops`, `authoring`, `mcp-clients`, each **1.0.0**, core
`>=0.21.0,<1.0.0`. The installer writes each agent as version 1 with the author
`plugin:<plugin>/<slug>@<catalogue version>` (e.g. `plugin:software-delivery/architect@2`).

## Agents

The fingerprint is `EntreeDuCatalogue.empreinte` (sha256 of the entry's document once loaded, context
included) at the source commit; `scripts/check_core_parity.py` recomputes both sides in CI.

| Slug | Kind | Plugin | Plugin version | Catalogue version | Core file | Fingerprint (`EntreeDuCatalogue.empreinte`) |
|:--|:--|:--|:--|--:|:--|:--|
| `agent-drafter` | internal | `authoring` | 1.0.0 | 1 | `agents/agent-drafter.yaml` | `sha256:e61d813ff1b29126450cb96eca86e243a2b17f909356735d1f0fdb453c77c7dc` |
| `architect` | internal | `software-delivery` | 1.0.0 | 2 | `agents/architect.yaml` | `sha256:8785fc6b47bc90e51470f6b6db015c0f0c82bb9dee5db7a16f3bc85675db2164` |
| `ci-fixer` | internal | `software-delivery` | 1.0.0 | 1 | `agents/ci-fixer.yaml` | `sha256:a9657bbd891e94ec8cc89cb4f76863a2acd9ba65ed55fa8bd005e485766cbc9a` |
| `compliance` | internal | `software-delivery` | 1.0.0 | 1 | `agents/compliance.yaml` | `sha256:b83e56a1b4885dcc39cf45b6672314d0101dff37879a21b526879cc6eb0c0189` |
| `developer` | internal | `software-delivery` | 1.0.0 | 1 | `agents/developer.yaml` | `sha256:54745b0d6494f24a871f13fc5ac09fcc94b6ab8ef7e1dd5bfeb533d3e28d7abc` |
| `devops` | internal | `software-delivery` | 1.0.0 | 1 | `agents/devops.yaml` | `sha256:0dd95591c26cbb98087bd64724993a0e044fb37ac5a5d5f60b8432e7c8b63cb9` |
| `docs-writer` | internal | `software-delivery` | 1.0.0 | 1 | `agents/docs-writer.yaml` | `sha256:edbf22b2a34600f5db04a8401fa4d449c7a8fbf89fd160357b911e4d3eac3c5f` |
| `knowledge-distiller` | internal | `authoring` | 1.0.0 | 1 | `agents/knowledge-distiller.yaml` | `sha256:1042217afe3a991726ca16e88ddf24acb40e29eaca9d3f7a431fbf7e02864902` |
| `netops` | internal | `netops` | 1.0.0 | 2 | `agents/netops.yaml` | `sha256:28254e29b5058e76551d13abc29ba2ce4e51966cc00aae22b734a95d77048daf` |
| `planner` | internal | `software-delivery` | 1.0.0 | 1 | `agents/planner.yaml` | `sha256:38c637ad8489248a88b1ee6bd8338018ec295b8c7e8e4b9cda46bfd0a9dd3986` |
| `prod-verifier` | internal | `software-delivery` | 1.0.0 | 2 | `agents/prod-verifier.yaml` | `sha256:ff80ea3a831bb99bbe21f46190c72ffd74de2b826738042ff30d8576a6a79df8` |
| `qa` | internal | `software-delivery` | 1.0.0 | 1 | `agents/qa.yaml` | `sha256:1d11d469562a56f4109c99283b1a375d0ea2889c99f218596c01b8ed9a581374` |
| `release-notes-writer` | internal | `software-delivery` | 1.0.0 | 1 | `agents/release-notes-writer.yaml` | `sha256:1197091c323675ab8858b7038a033775cc7ee57991cb0a5e640384a71adc3999` |
| `researcher` | internal | `software-delivery` | 1.0.0 | 1 | `agents/researcher.yaml` | `sha256:3690f59b911f440eb60ac1e597289e07055038f5122a4495b704c55ef9581995` |
| `review-responder` | internal | `software-delivery` | 1.0.0 | 1 | `agents/review-responder.yaml` | `sha256:6aad4fb11a2b4b095b24d93ba1f67209f930ded1dcaac6476595168c95d2280d` |
| `reviewer` | internal | `software-delivery` | 1.0.0 | 1 | `agents/reviewer.yaml` | `sha256:9d02fde54a0c1e3a3fa205ca54b042c8b9430fc30f618372a6768743b8fa283b` |
| `secops` | internal | `software-delivery` | 1.0.0 | 1 | `agents/secops.yaml` | `sha256:c48a36fc89668274932cc815765c03959198987819938dfdbeabb585c234c83e` |
| `security-reviewer` | internal | `software-delivery` | 1.0.0 | 1 | `agents/security-reviewer.yaml` | `sha256:ec4b2763979c34ae5c89c11d7f8db09b825a7ca4463fb88231ae3e132668a13d` |
| `spec-writer` | internal | `software-delivery` | 1.0.0 | 2 | `agents/spec-writer.yaml` | `sha256:f0a7a2f5205981e58a4d105e69e60bdec59fd9fee7d593e59288e972528c0f76` |
| `tester` | internal | `software-delivery` | 1.0.0 | 1 | `agents/tester.yaml` | `sha256:3073ea61f6ab8e3eed4d5cb101f613139d883bea34fff2c590cb604cd1b4aa92` |
| `triager` | internal | `software-delivery` | 1.0.0 | 1 | `agents/triager.yaml` | `sha256:c83ff15d29e5fb9b144b866d6755b7e017939ee75becf834554cdbab3e159775` |
| `ux-designer` | internal | `software-delivery` | 1.0.0 | 1 | `agents/ux-designer.yaml` | `sha256:c82e0055357e8df0b61ca72ee07cd27851b9e57d6d88e859ee63f7280bcc6e8b` |
| `chatgpt` | external | `mcp-clients` | 1.0.0 | 1 | `clients/chatgpt.yaml` | `sha256:c8381ed40acbc2888c3c4bf12ebcd9e6a49bf3edab1f7d7c070c685b7ba90af3` |
| `claude-ai` | external | `mcp-clients` | 1.0.0 | 1 | `clients/claude-ai.yaml` | `sha256:56cbd162bc57f803e6e162baf922669d6a50a3d205a521a88b69efc2b3113496` |
| `claude-code` | external | `mcp-clients` | 1.0.0 | 1 | `clients/claude-code.yaml` | `sha256:9b26f873b0cea50cd208f055512bea877f3743addce82438d7c0d590321f836f` |
| `claude-desktop` | external | `mcp-clients` | 1.0.0 | 1 | `clients/claude-desktop.yaml` | `sha256:39de108874699603fad6781e387cff032c6502304e3df228f11baaca217bdd89` |
| `copilot-vscode` | external | `mcp-clients` | 1.0.0 | 1 | `clients/copilot-vscode.yaml` | `sha256:5d43fcb4c391dba5250bc056ddb8df3ab03ad735ffb6c610ffac1bf64422172a` |
| `cursor` | external | `mcp-clients` | 1.0.0 | 1 | `clients/cursor.yaml` | `sha256:105c9636b27da7f6df85564bfe58719db587d4f0ac20281939b10e1c914eef94` |

## Instructions are byte-identical

- Each agent file is the core's file. Only YAML comments changed (the six client files: their French
  comments translated); the parsed document is equal, which the parity check asserts.
- The plugin files do **not** inline `_contexte.md`. The installer loads a plugin agent through the
  core's own `entry_of` (`services/plugin_installer.py`), which prepends `_contexte.md` to an internal
  agent's `spec.instructions` exactly as the built-in loader does. Installed instructions are therefore
  `_contexte.md` + the entry's instructions, byte for byte today's, and the agent's checksum
  (`services.agents.empreinte(spec)`) is the same. Inlining the context would have doubled it.
- External clients (`kind: external`) get no context, as in the core.

## Eval cases

Every file of `eval_cases/` is carried unchanged, next to its agent: `<plugin>/evals/<same file name>`
(`architect.yaml` and `architect-accept.yaml`, `devops.yaml` and `devops-verify.yaml`,
`prod-verifier.yaml` and `prod-verifier-with-indicators.yaml` in `software-delivery`; `netops.yaml` in
`netops`; `agent-drafter.yaml`, `knowledge-distiller.yaml` in `authoring`). The clients have none, as in
the core. The installer plays each one; all pass.

## Skills

| Skill | Plugin | Files |
|:--|:--|:--|
| `madr-4` | `software-delivery` | `SKILL.md`, `template.md`, `template-minimal.md` byte-identical to the core's; **plus** `LICENSE` (MADR's MIT text) and `PROVENANCE.json` (upstream adr/madr 4.0.0, `2475fe19`) |

Because of the two added files, the skill's checksum differs from the core's `madr-4`: an organisation
that holds the core's version 1 is offered the plugin's as a new version unless the migration compares
the three shared files only.

## What the migration must also handle

- **Second approval.** The gate rates `madr-4` high risk (`outbound_url`: the core's own files link to
  adr.github.io), so installing `software-delivery` waits for a second administrator, a default install
  included. The other three plugins install without one.
- **Core code that reads the catalogue by slug** keeps needing an entry: `agent_drafts.py`
  (`agent-drafter`), `knowledge/distill.py` (`knowledge-distiller`), `gabarits.py`, `routers/agents.py`,
  `services/catalogue_d_agents.py`, `choregos_playbooks.evals.agents`, and the migration
  `20261016_1500_template_catalogue_provenance`.
- **Client metadata.** `catalogue.client` and `catalogue.reach` of the six clients are read from the core's
  catalogue by `services/catalogue_d_agents.py` (which client the Integrations page offers); the plugin
  installer does not keep them.
- **Templates.** The core's `github-software-delivery` and `software-delivery-governed` templates name
  19 agents, all in `software-delivery`: one `requires.plugins` entry covers them.
- **`requires.org_connectors`** of `netops` (`pfsense`, `cilium`, `nginx`: `mcp`) is declarative; the
  installer does not check it.
