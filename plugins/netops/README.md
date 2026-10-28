# Network operations plugin

The `netops` agent of the core's built-in catalogue (ADR 0040, ADR 0052), unchanged. It reads through the organisation's MCP connectors named `pfsense`, `cilium` and `nginx` (`requires.org_connectors`), and proposes every change as code; it applies none.

| Agent | Catalogue version | What it does |
|:--|--:|:--|
| [`netops`](agents/netops.yaml) | 2 | Diagnoses network behaviour from flows, policies and firewall rules; proposes every change as code, never applies one. |

Version 1.0.0, core `>=0.21.0,<1.0.0`. The instructions are the core's, byte for byte: the plugin's files
do not hold the work-item context, which the core prepends when it installs an internal agent. See
[MIGRATION-FROM-CORE.md](../../MIGRATION-FROM-CORE.md).

```sh
choregos plugin validate plugins/netops
choregos plugin pack plugins/netops -o netops-1.0.0.tar.gz   # same bytes every time
```
