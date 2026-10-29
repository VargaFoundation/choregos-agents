# Choregos agents

Agents for [Choregos](https://github.com/VargaFoundation/choregos), the platform of governed agents,
each packaged as a **plugin** the platform installs by itself.

A plugin is one directory with a `plugin.yaml` at its root (`apiVersion: choregos/v1`, `kind: Plugin`).
It carries what an agent needs to do its job:

| Part | What it is |
|:--|:--|
| `agents/` | the agents, in the catalogue entry shape (`catalogue:` + `agent:`) |
| `skills/` | `SKILL.md` directories the agents carry |
| `templates/` | project templates: workflows, routing, policy |
| `evals/` | evaluation cases run before a version is released |
| `procedures/` | step-by-step guides for whoever installs and configures it |
| `servers/` | when the agent needs code to reach a system: an MCP server image the platform runs and governs |

The format is specified by ADR 0093 and ADR 0094 in the Choregos repository, and checked by
`choregos plugin validate`.

## Plugins

| Plugin | What it does |
|:--|:--|
| [`servicenow`](plugins/servicenow) | Triage ServiceNow incidents under human approval: the `itsm-triager` agent, an `itsm-incident-triage` template, the MCP server over the Table API (tracker included), the instance set-up procedure |
| [`software-delivery`](plugins/software-delivery) | The 19 agents of a governed software delivery, from triage to the production go or no-go, with the `madr-4` skill and their evals |
| [`netops`](plugins/netops) | The `netops` agent: network diagnosis through the pfSense, Cilium and nginx connectors, every change proposed as code |
| [`authoring`](plugins/authoring) | The agents that draft an organisation's agents and distil its knowledge bases into skills |
| [`mcp-clients`](plugins/mcp-clients) | The external MCP clients: ChatGPT, Claude (claude.ai, Code, Desktop), Copilot in VS Code, Cursor |
| [`requests`](plugins/requests) | The agents of the core's `default-request` workflow, for requests from any trade with no repository: `brief-writer` writes the brief a person approves, `assistant` produces the deliverable a person accepts |

`software-delivery`, `netops`, `authoring` and `mcp-clients` are the core's built-in agent catalogue, moved here unchanged; see
[MIGRATION-FROM-CORE.md](MIGRATION-FROM-CORE.md).

## Installing

A Choregos deployment reads plugins from **sources**. This repository is the source `varga`:

```yaml
plugins:
  sources:
    - name: varga
      index: oci://ghcr.io/vargafoundation/choregos-agents/index
      trust:
        issuer: https://token.actions.githubusercontent.com
        identity: https://github.com/VargaFoundation/choregos-agents/.github/workflows/publish.yml@refs/heads/main
  defaultPlugins:
    - {source: varga, name: servicenow, version: "^0.1"}
```

Plugins listed in `defaultPlugins` are installed in every organisation at start-up; the others are
installed from the console (Catalogue → Plugins).

## Publishing your own plugins

Nothing here is special to this repository. Any repository that publishes plugins as signed OCI
artifacts, with an `index.yaml`, is a source a deployment can add next to `varga`. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the layout and the checks.

## Licence

Apache License 2.0. Every commit certifies the [Developer Certificate of Origin](DCO).
