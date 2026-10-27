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
| [`servicenow`](plugins/servicenow) | Triage ServiceNow incidents under human approval *(in progress)* |

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
