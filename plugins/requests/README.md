# Requests plugin

The agents of the core's `default-request` workflow (ADR 0110), the one a project without a repository receives: a request from any trade becomes a brief a person approves, then a deliverable a person accepts or sends back. Their instructions are the core's `brief` and `deliver` playbooks (VargaFoundation/choregos#1091).

| Agent | Catalogue version | What it does |
|:--|--:|:--|
| [`assistant`](agents/assistant.yaml) | 1 | Produces the deliverable an approved brief asks for, citing every fact it takes from a source, and checks it against the brief's acceptance criteria; needs no repository. |
| [`brief-writer`](agents/brief-writer.yaml) | 1 | Turns a request from any trade into a one-page brief a person approves before anyone works on it; produces no deliverable and needs no repository. |

Version 1.0.1, core `>=0.21.0,<1.0.0` (1.0.0 required a core after 0.21.0, which a core built from main does not report). The plugin's files do not hold the work-item context, which the core
prepends when it installs an internal agent.

```sh
choregos plugin validate plugins/requests
choregos plugin pack plugins/requests -o requests-1.0.0.tar.gz   # same bytes every time
```
