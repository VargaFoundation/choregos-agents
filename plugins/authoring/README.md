# Authoring plugin

The agents that write for the organisation itself, from the core's built-in catalogue (ADR 0040), unchanged: an agent drafted from a person's description, and a knowledge base compiled into a principles skill. A person reviews what both write.

| Agent | Catalogue version | What it does |
|:--|--:|:--|
| [`agent-drafter`](agents/agent-drafter.yaml) | 1 | Drafts an agent (instructions, suggested skills, knowledge and connectors, a sample, eval cases) from a person's description; a person reviews it. |
| [`knowledge-distiller`](agents/knowledge-distiller.yaml) | 1 | Compiles a knowledge base into a principles skill, each principle citing the documents it comes from; a person approves it. |

Version 1.0.0, core `>=0.21.0,<1.0.0`. The instructions are the core's, byte for byte: the plugin's files
do not hold the work-item context, which the core prepends when it installs an internal agent. See
[MIGRATION-FROM-CORE.md](../../MIGRATION-FROM-CORE.md).

```sh
choregos plugin validate plugins/authoring
choregos plugin pack plugins/authoring -o authoring-1.0.0.tar.gz   # same bytes every time
```
