# itsm-incident-triage — ServiceNow incidents triaged under approval

One workflow, `incident-triage`, for a service desk that wants an agent to prepare the triage of each
incident of its queue, and a person to decide what is written back.

| Step | Who | What happens | Leaves on failure or refusal |
|:--|:--|:--|:--|
| queue | — | an incident of the assignment groups becomes a work item (Business Rule, or polling) | — |
| triage | agent `itsm-triager` | reads the incident, journal, CI and relations, similar incidents and published articles; writes `itsm_triage_markdown`; any write it calls waits in Approvals | back to the queue, untouched |
| review | people of `service-desk` | decide the proposals in Approvals; settle the classification and the work note in the form | **reject** → back to the queue, untouched |
| apply | the platform | one governed action: `update_record` (category, impact, urgency, assignment group) then `add_work_note` | retried, then a person |
| done | — | — | — |

Things to know:

- **Nothing is written before a person decides.** The triager's own calls are governed actions; the apply
  step writes only what the review form holds. Priority is never written: ServiceNow computes it.
- **A customer comment or a resolution** is proposed only when a published knowledge article or a resolved
  incident gives the fix, and always waits for a person (`human_approval` in the server's descriptor).
- **The work item's `sys_id`** is the incident's, as the server's tracker profile returns it.

## What the template format cannot say yet

The project's tracker is the ServiceNow server of this same plugin (`implements: [tracker]`), read
through the core's `tracker/plugin` adapter (S43-07). `template.schema.json` has no way to name a plugin
server as the tracker, so the manifest writes `tracker: plugin:servicenow/servicenow` — a convention
mirroring the webhook route `/webhooks/plugins/{plugin}/{server}` — until S43-07 settles the form.

Validated by `choregos plugin validate` (manifest) and the core's workflow parser (strict); not yet run
end to end on a deployment.
