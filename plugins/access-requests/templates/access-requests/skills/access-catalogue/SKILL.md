---
name: access-catalogue
description: Which tool opens which directory group, by its id, whether the group is sensitive, and whether someone may ask for a colleague.
---

# Access catalogue

**Replace this table with your organisation's**: publish a new version of this skill in the organisation
(a project born later never rewrites it). Each `group_id` is the directory group's object id (a GUID: the
workflow refuses anything else), and it is what the approver reads first. The `directory` connector only
grants accounts inside its administrative unit.

| Tool | Directory group | `group_id` | Sensitive | For someone else |
|:--|:--|:--|:--|:--|
| Wiki | `app-wiki-users` | `00000000-0000-0000-0000-000000000001` | no | yes |
| Design files | `app-design-users` | `00000000-0000-0000-0000-000000000002` | no | yes |
| CRM | `app-crm-users` | `00000000-0000-0000-0000-000000000003` | no | yes |
| Analytics | `app-analytics-readers` | `00000000-0000-0000-0000-000000000004` | no | no |
| Production read | `prod-read` | `00000000-0000-0000-0000-000000000005` | yes | no |
| Finance exports | `finance-exports` | `00000000-0000-0000-0000-000000000006` | yes | no |

## Rules

- A tool or group missing from this table is never granted: the request is `other`, and the summary
  names what was asked.
- Several tools may share a group: grant the group once.
- **For someone else** `no`: only the person themselves may ask; a request naming someone else is
  `other`. `yes`: anyone may ask for a colleague, and the approver sees who asked for whom. The
  directory read gives no manager or membership: who may ask for whom is not checked beyond this column.
- A sensitive group waits for the same approval as any other; the approver sees that it is sensitive.
