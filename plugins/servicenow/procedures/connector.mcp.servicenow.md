---
id: connector.mcp.servicenow
title: Connect a ServiceNow instance
applies_to: {connector: mcp/servicenow}
audience: org_admin
version: 1
reviewed: 2026-10-27
vendor_docs:
  - {title: "Configure OAuth client credentials grant (inbound)", url: "https://www.servicenow.com/docs/bundle/zurich-platform-security/page/administer/security/task/client-credentials.html"}
  - {title: "Table API", url: "https://www.servicenow.com/docs/bundle/zurich-api-reference/page/integrate/inbound-rest/concept/c_TableAPI.html"}
  - {title: "Business rules", url: "https://www.servicenow.com/docs/bundle/zurich-api-reference/page/script/business-rules/concept/c_BusinessRules.html"}
  - {title: "RESTMessageV2 API", url: "https://www.servicenow.com/docs/bundle/zurich-api-reference/page/app-store/dev_portal/API_reference/RESTMessageV2/concept/c_RESTMessageV2API.html"}
egress: ["<instance>.service-now.com"]
values: ["plugin_webhook_url:servicenow/servicenow"]
fields:
  base_url: "https://<instance>.service-now.com, without a trailing slash"
  auth: "oauth (client credentials), or basic for an older release or a Personal Developer Instance"
  client_id: "the Client ID of the OAuth inbound integration"
  client_secret: "its Client Secret, typed once"
  username: "the integration user (auth=basic only)"
  password: "its password (auth=basic only), typed once"
  webhook_secret: "a random string of at least 32 characters, the same as in the Business Rule"
  assignment_groups: "the names (or sys_ids) of the groups whose queue Choregos triages"
---

## Before you start

- A ServiceNow instance you administer. A free Personal Developer Instance (PDI) from the ServiceNow
  Developer Program is enough to try the plugin.
- The `servicenow` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in the
  platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- The assignment groups whose incidents Choregos should triage (for instance `Service Desk`).
- A random secret of at least 32 characters for the webhook, kept in your secret store. You type it twice:
  in Choregos and in a ServiceNow system property.
- This procedure is written from ServiceNow's documentation and the plugin's fake instance; it has not yet
  been run end to end against a live instance.

## At ServiceNow

1. **The integration user.** System Security, Users, **New**: user ID `choregos.integration`, check
   **Web service access only** and **Internal Integration User**. Do not give it `admin` or `itil`.
2. **The custom role.** System Security, Roles, **New**: `choregos_integration`. Give it to the
   integration user only. Its rights are listed in Permissions below.
3. **The OAuth inbound integration** (auth `oauth`).
   - On Washington DC and later, set the system property
     `glide.oauth.inbound.client.credential.grant_type.enabled` to `true` (create it as a true/false
     property if it does not exist).
   - System OAuth, Application Registry, **New**, **Create an OAuth API endpoint for external clients**
     (on newer releases: **New Inbound Integration Experience**, **OAuth - Client credentials grant**).
     Name it `Choregos`. Set **OAuth Application User** to `choregos.integration`, so every call runs with
     the custom role and nothing more. Save, then copy the **Client ID** and **Client Secret**.
   - On an older release, or a PDI without the client credentials grant, use auth `basic` with the
     integration user's password instead.
4. **The webhook properties.** System Properties, **New**, twice, both with **Read roles** and
   **Write roles** set to `admin`:
   - `choregos.webhook_url`, type string: `{{plugin_webhook_url:servicenow/servicenow}}`;
   - `choregos.webhook_secret`, type string: the webhook secret.
5. **The Business Rule.** System Definition, Business Rules, **New**: name `Choregos - notify`, table
   **Incident [incident]**, check **Advanced**. When: **after**, on **Insert** and **Update**. Filter
   conditions: **Assignment group** *is one of* the groups you chose. Script:

   ```javascript
   (function executeRule(current, previous /*null when async*/) {
       var url = gs.getProperty('choregos.webhook_url');
       var secret = gs.getProperty('choregos.webhook_secret');
       if (!url || !secret) {
           gs.warn('Choregos: choregos.webhook_url or choregos.webhook_secret is not set');
           return;
       }
       var operation = current.operation();
       if (operation == 'update' && current.comments.changes()) {
           operation = 'comment';
       }
       var request = new sn_ws.RESTMessageV2();
       request.setEndpoint(url);
       request.setHttpMethod('post');
       request.setRequestHeader('Content-Type', 'application/json');
       request.setRequestHeader('X-Choregos-Secret', secret);
       request.setHttpTimeout(10000);
       request.setRequestBody(JSON.stringify({
           table: current.getTableName(),
           sys_id: current.getUniqueValue(),
           number: current.getValue('number'),
           sys_mod_count: parseInt(current.getValue('sys_mod_count'), 10),
           operation: operation,
           state: current.getValue('state')
       }));
       request.executeAsync();
   })(current, previous);
   ```

   The payload names the record and nothing else: Choregos re-reads it through the Table API before
   acting, so nothing of the payload is trusted. The same rule on **Requested Item [sc_req_item]** or
   **Change Request [change_request]** makes those work items too.
6. Without the Business Rule, Choregos polls the assignment groups' queue instead, with a delay.

## Permissions

The custom role `choregos_integration` holds table ACLs, nothing else: no `admin`, no `itil`, no
`security_admin`, no script or system property rights.

- **Read** (records and fields) on `incident`, `sc_req_item`, `change_request`, `problem`,
  `kb_knowledge`, `cmdb_ci`, `cmdb_rel_ci`, `sys_user_group`, `sys_choice` and `sys_journal_field`, and on
  the `name` and `vip` fields of `sys_user` (the server reads the caller's VIP flag).
- **Write** on `incident`: `work_notes`, `comments`, `category`, `subcategory`, `impact`, `urgency`,
  `assignment_group`, `assigned_to`, `cmdb_ci`, and for resolving and reopening `state`, `close_code`,
  `close_notes`. Never `priority`: ServiceNow computes it.
- **Write** on `sc_req_item` and `change_request`: `work_notes` and `state` (the tracker's state and status
  notes).
- **Create** on `incident` (the tracker can open one) and on `change_request` (`create_change_request`,
  always approved by a person).

In Choregos, the server's descriptor is the floor: reads are allowed; work notes and field updates go
through approval; a customer comment, a resolution and a change request always wait for a person, and an
organisation can only make that stricter.

## In Choregos

1. Catalogue, **Plugins**, `servicenow`, **Configure**.
2. `base_url`: `https://<instance>.service-now.com`. `auth`: `oauth` (or `basic`).
3. `client_id` and `client_secret` from step 3 (or `username` and `password` for `basic`): sealed, shown
   once.
4. `webhook_secret`: the same secret as the `choregos.webhook_secret` property.
5. `assignment_groups`: the groups the Business Rule filters on.
6. Create a project from the template **ServiceNow · incident triage under approval**. Put the people who
   review the triage in the `service-desk` group.

## Test it

1. Press **Test** on the connector: `search_records` on `incident` answers with the queue's incidents.
2. In ServiceNow, create an incident assigned to one of the groups. Within a minute it appears on the
   project's board in **In the queue**, then the triager runs.
3. System Logs, **Outbound HTTP Requests**: the Business Rule's POST answered `200`.
4. Approve the triage in Choregos: a work note signed `[choregos:…]` appears on the incident.

## When it fails

- `401` from the instance: wrong client ID or secret, the client credentials grant is not enabled, or the
  OAuth Application User is empty. With `basic`, check **Web service access only** and the password.
- `403` on a read or a write: the custom role lacks that table or field ACL (see Permissions). The server
  says which table and field.
- The webhook answers `401`: the `X-Choregos-Secret` sent differs from `webhook_secret`, or a property is
  empty (the system log shows `Choregos: … is not set`).
- No outbound request in the log: the Business Rule's filter does not match the incident's assignment
  group, or the rule is inactive.
- A timeout from Choregos: the egress rule of the plugin's server must allow the instance's host.
- The VIP flag is always false: the role cannot read `sys_user.vip`.
