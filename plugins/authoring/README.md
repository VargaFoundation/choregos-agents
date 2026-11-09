# Authoring plugin

The agents that write for the organisation itself, from the core's built-in catalogue (ADR 0040), unchanged: an agent drafted from a person's description, and a knowledge base compiled into a principles skill. A person reviews what both write.

| Agent | Catalogue version | What it does |
|:--|--:|:--|
| [`agent-drafter`](agents/agent-drafter.yaml) | 2 | Drafts an agent (instructions, suggested skills, knowledge and connectors, a sample, eval cases) from a person's description; a person reviews it. |
| [`knowledge-distiller`](agents/knowledge-distiller.yaml) | 2 | Compiles a knowledge base into a principles skill, each principle citing the documents it comes from; a person approves it. |

Version 1.1.0, core `>=0.25.0,<1.0.0`. The instructions are the core's, byte for byte: the plugin's files
do not hold the work-item context, which the core prepends when it installs an internal agent. See
[MIGRATION-FROM-CORE.md](../../MIGRATION-FROM-CORE.md).


## Declared components (1.1.0)

Every agent declares the five components of ADR 0127 (the core's S60-04), so that a version no longer
inherits any of them from its project. Each agent's catalogue version is one higher than in 1.0.0, and no
instruction changed. `tools.web` and `tools.extensions` are declared even when empty: a list that is present
closes its source. The project and the deployment still only tighten what is named here.

| Agent | Model | `catalogue` | `extensions` | `web` | `memories` | Why |
|:--|:--|:--|:--|:--|:--|:--|
| `agent-drafter` | `profile:standard` | none | none | none | none | One turn, no tool: the platform calls it with the lists of skills, knowledge and connectors it may suggest, and `standard` is the tier it is called with. |
| `knowledge-distiller` | `profile:standard` | none | none | none | none | One turn over the documents it is handed; `standard` is the tier the distiller uses. It runs on a knowledge base, in no project, so no project memory. |

`profile:standard` is the tier the API already asks for these two (`services/agent_drafts.py`, `knowledge/distill.py`).

```sh
choregos plugin validate plugins/authoring
choregos plugin pack plugins/authoring -o authoring-1.1.0.tar.gz   # same bytes every time
```
