# Requests plugin

The agents of the core's `default-request` workflow (ADR 0110), the one a project without a repository receives: a request from any trade becomes a brief a person approves, then a deliverable a person accepts or sends back. Their instructions are the core's `brief` and `deliver` playbooks (VargaFoundation/choregos#1091).

| Agent | Catalogue version | What it does |
|:--|--:|:--|
| [`assistant`](agents/assistant.yaml) | 2 | Produces the deliverable an approved brief asks for, citing every fact it takes from a source, and checks it against the brief's acceptance criteria; needs no repository. |
| [`brief-writer`](agents/brief-writer.yaml) | 2 | Turns a request from any trade into a one-page brief a person approves before anyone works on it; produces no deliverable and needs no repository. |

Version 1.1.0, core `>=0.24.9,<1.0.0`; an older core keeps 1.0.1. The plugin's files do not hold the work-item context, which the core
prepends when it installs an internal agent.


## Declared components (1.1.0)

Every agent declares the five components of ADR 0127 (the core's S60-04), so that a version no longer
inherits any of them from its project. Each agent's catalogue version is one higher than in 1.0.1, and no
instruction changed. `tools.web` and `tools.extensions` are declared even when empty: a list that is present
closes its source. The project and the deployment still only tighten what is named here.

| Agent | Model | `catalogue` | `extensions` | `web` | `memories` | Why |
|:--|:--|:--|:--|:--|:--|:--|
| `brief-writer` | `profile:by_size` | none | none | none | `project/shared` write | Reads knowledge bases (declared by the version, not here); asks with `ask_human` (the frame). It names sources, it never fetches them. |
| `assistant` | `profile:by_size` | none | none | `fetch` | `project/shared` write | Its sources are the ones the brief names, among them a page: `web_fetch` stays bounded by the run's network. It cites tools by name and no portable name exists for the catalogue tools a deployment opens, so `catalogue` is empty: a deployment that relies on one publishes its own version of this agent. |

`profile:by_size` is what these agents ran with unless a workflow actor overrode it; it is an explicit choice
here, not a new one. The memory is what the legacy `memory: project` meant (`effective_bindings`): the run's
lessons go to the project's shared memory. A later version may narrow it.

```sh
choregos plugin validate plugins/requests
choregos plugin pack plugins/requests -o requests-1.1.0.tar.gz   # same bytes every time
```
