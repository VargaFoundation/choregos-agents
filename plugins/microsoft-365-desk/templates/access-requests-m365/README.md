# access-requests-m365 — access requests and IT desk, on Microsoft 365

The same agent, skills and governed writes as the `access-requests` plugin's template
([`access-requests`](../../../access-requests/templates/access-requests/README.md)), on a
different tracker: the `microsoft-365-desk` plugin's server, which turns the messages of the desk's shared
mailbox and Teams channel into work items. Every outcome is answered in the requester's thread by its own
action, after the write it reports.

| Name | Capability | For |
|:--|:--|:--|
| `directory` | `identity` (`entra`) | reading a person, adding them to a group, and its compensation |
| `desk` | `mcp` (the `microsoft-365-desk` plugin's server) | `reply`: an answer in the thread a request came from |

| Workflow | What it adds to `access-requests` |
|:--|:--|
| [`access-request`](workflows/access-request.yaml) | what the agent does not sort goes to a person as a task: their answer is sent in the thread |
| [`access-grant`](workflows/access-grant.yaml) | "added" or "not granted", told in the thread |
| [`bug-report`](workflows/bug-report.yaml) | "filed in `<project>`", told in the thread |

The requester is the message's sender, as the desk tracker gives it (`fields.tracker.reporter`). The
project is born from `plugin:microsoft-365-desk/access-requests-m365`, once this plugin's server runs.
What happens to a request, the set-up of the approvers, the catalogue and the solutions, and what it
does not do yet: the `access-requests` plugin's [README](../../../access-requests/README.md).
