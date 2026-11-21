---
name: solutions
description: Which project of the organisation holds each solution's bugs, so a qualified bug report is filed in the right tracker.
---

# Solutions

**Replace this table with your organisation's**: publish a new version of this skill in the organisation.
Each project named here must accept work items from the desk's project (its config's
`accepts_items_from` lists it); its tracker — Jira, GitHub, GitLab or another — receives the report.

| Solution | Also called | Project |
|:--|:--|:--|
| Expense reports | expenses, expense tool | `expenses` |
| Customer portal | portal, extranet | `customer-portal` |
| Payroll export | payroll | `payroll` |

## Rules

- A solution missing from this table is not filed: the request is `other`, and the summary names it.
- `project` is copied from this table, never guessed from the solution's name.
