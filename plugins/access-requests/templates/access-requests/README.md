# access-requests — access requests and IT desk, from a trigger

Three workflows, one agent and three skills, installed in the organisation when a project is born from
the template. The project's tracker is internal: requests come from a trigger. The grants call one
**organisation** connector, by name; declaring it is the organisation administrator's job:

| Name | Capability | For |
|:--|:--|:--|
| `directory` | `identity` (`entra`) | reading a person (`get_user`), adding them to a group (`add_to_group`), and its compensation (`remove_from_group`) |

| Workflow | What it does |
|:--|:--|
| [`access-request`](workflows/access-request.yaml) (default) | the `request-desk` agent sorts the request into `access`, `bug` or `other`; access opens one `access-grant` per group, a bug one `bug-report`; the rest goes to a person |
| [`access-grant`](workflows/access-grant.yaml) | one person into one group, as one governed action the tool's approvers decide — never the person it grants nor who asked |
| [`bug-report`](workflows/bug-report.yaml) | one qualified report, filed in the solution's project (`work_item.file`) once a member of `it-desk` approves |

| Skill | What it holds |
|:--|:--|
| [`request-desk`](skills/request-desk/SKILL.md) | how a request is sorted, what a grant and a bug report hold |
| [`access-catalogue`](skills/access-catalogue/SKILL.md) | tool → directory group id → sensitive → approvers → may a colleague ask; **replace it with yours** |
| [`solutions`](skills/solutions/SKILL.md) | solution → the project that holds its bugs; **replace it with yours** |

The fields a trigger may set on a request: `requester` (sign-in name or e-mail), `channel` and
`source_ref`. See the plugin's [README](../../README.md) for the set-up and what the template does not do
yet.
