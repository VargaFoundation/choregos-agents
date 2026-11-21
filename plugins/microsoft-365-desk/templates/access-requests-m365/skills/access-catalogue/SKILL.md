---
name: access-catalogue
description: Which tool opens which directory group, by its id, whether the group is sensitive, who approves it, and whether someone may ask for a colleague.
---

# Access catalogue

**Replace this table with your organisation's**: publish a new version of this skill in the organisation
(a project born later never rewrites it). Each `group_id` is the directory group's object id (a GUID: the
workflow refuses anything else), and it is what the approver reads first. The `directory` connector only
grants accounts inside its administrative unit. **Approvers** names the suffix of the identity-provider
group whose members approve the grant: `crm` means `access-approvers-crm`. Give a sensitive group a
stricter group of approvers.

| Tool | Directory group | `group_id` | Sensitive | Approvers | For someone else |
|:--|:--|:--|:--|:--|:--|
| Wiki | `app-wiki-users` | `00000000-0000-0000-0000-000000000001` | no | `wiki` | yes |
| Design files | `app-design-users` | `00000000-0000-0000-0000-000000000002` | no | `design` | yes |
| CRM | `app-crm-users` | `00000000-0000-0000-0000-000000000003` | no | `crm` | yes |
| Analytics | `app-analytics-readers` | `00000000-0000-0000-0000-000000000004` | no | `data` | no |
| Production read | `prod-read` | `00000000-0000-0000-0000-000000000005` | yes | `platform-leads` | no |
| Finance exports | `finance-exports` | `00000000-0000-0000-0000-000000000006` | yes | `finance-leads` | no |

## Rules

- A tool or group missing from this table is never granted: the request is `other`, and the summary
  names what was asked.
- Several tools may share a group: grant the group once.
- **For someone else** `no`: only the person themselves may ask; a request naming someone else is
  `other`. `yes`: anyone may ask for a colleague, and the approver sees who asked for whom. The
  directory read gives no manager or membership: who may ask for whom is not checked beyond this column.
- A sensitive group waits for its own approvers, re-authenticated; the approver sees that it is
  sensitive. Neither the person it grants nor the person who asked may approve it, whatever their rank.
