---
id: connector.mcp.servicenow
title: Connect a ServiceNow instance
applies_to: {connector: mcp/servicenow}
audience: org_admin
version: 2
reviewed: 2026-11-21
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
  assignment_groups: "the names (or sys_ids) of the groups whose queue Tranibor triages"
---

## Before you start

- A ServiceNow instance you administer. A free Personal Developer Instance (PDI) from the ServiceNow
  Developer Program is enough to try the plugin.
- The `servicenow` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in the
  platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- The assignment groups whose incidents Tranibor should triage (for instance `Service Desk`).
- A random secret of at least 32 characters for the webhook, kept in your secret store. You type it twice:
  in Tranibor and in a ServiceNow system property.
- This procedure was run on a Personal Developer Instance of the **Australia** release (patch 7) on
  2026-11-21, by the server's live suite (`server/tests/live`), with both authentications. What that run did
  not reach: the Business Rule's delivery to Tranibor itself (the instance needs a public URL for the
  webhook; the rule's requests were read from its outbound HTTP log instead).

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
     Name it `Tranibor`. Set **OAuth Application User** to `choregos.integration`, so every call runs with
     the custom role and nothing more. Save, then copy the **Client ID** and **Client Secret**.
     On Australia the registry record is an **OAuth Client** whose **Inbound Grant Type** is **Client
     Credentials** (`inbound_grant_type = client_credential`).
   - On an older release, or a PDI without the client credentials grant, use auth `basic` with the
     integration user's password instead. **From the release that enforces the `SNCRestrictBasicAuth` gate
     (Australia does), basic authentication of the API needs the role `snc_basic_auth_api_access`**: give it
     to the integration user, or every call answers `401 User is not authenticated` with the right
     password.
4. **The webhook properties.** System Properties, **New**, twice, both with **Read roles** and
   **Write roles** set to `admin`:
   - `choregos.webhook_url`, type string: `{{plugin_webhook_url:servicenow/servicenow}}`;
   - `choregos.webhook_secret`, type string: the webhook secret.
5. **The Business Rule.** System Definition, Business Rules, **New**: name `Tranibor - notify`, table
   **Incident [incident]**, check **Advanced**. When: **after**, on **Insert** and **Update**. Filter
   conditions: **Assignment group** *is one of* the groups you chose. Script:

   ```javascript
   (function executeRule(current, previous /*null when async*/) {
       var url = gs.getProperty('choregos.webhook_url');
       var secret = gs.getProperty('choregos.webhook_secret');
       if (!url || !secret) {
           gs.warn('Tranibor: choregos.webhook_url or choregos.webhook_secret is not set');
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

   The payload names the record and nothing else: Tranibor re-reads it through the Table API before
   acting, so nothing of the payload is trusted. The same rule on **Requested Item [sc_req_item]** or
   **Change Request [change_request]** makes those work items too.
6. Without the Business Rule, Tranibor polls the assignment groups' queue instead, with a delay.

On the Australia instance the rule posted one request per insert, update and comment, each answered with
its `sys_mod_count` (`server/tests/test_business_rule_deliveries.py` holds four of them). Tranibor's own
writes are updates too: each work note and status note it adds is posted back, and Tranibor re-reads the
item for nothing. A condition `gs.getUserName() != 'choregos.integration'` on the rule avoids that.

## Permissions

The custom role `choregos_integration` holds table ACLs, nothing else: no `admin`, no `itil`, no
`security_admin`, no script or system property rights. Creating an ACL needs the `security_admin` role,
elevated.

**Never create a field ACL (`incident.category`) or a wildcard one (`sys_user_group.*`) under a name that
has none.** ServiceNow decides a field by the most specific ACL that exists, in the order `table.field`,
`parent.field`, `*.field`, `table.*`, `parent.*`, `*.*`: a new ACL under a name that had none becomes the one
that decides **for every role**, and every other role loses the right the generic ACL gave it. On the
Australia instance, a new `sys_user_group.*` read ACL for this role took the read of every group field from
`itil` users, and a new `incident.correlation_id` create ACL took that field from them. Instead, for each
right below: find the ACL that decides it today, and either create one more ACL **under that same name**
for the role (ACLs under one name are alternatives: one is enough) or add the role to it (a
`sys_security_acl_role` row; it then grants the role whatever that ACL covers).

What the Australia run proved enough (with `admin_overrides`, each a new ACL under an existing name, except
the three marked *role added*):

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
- On that instance the record-level ACLs were new ones under the tables' own names (`incident`,
  `sc_req_item`, `change_request`, `problem`, `kb_knowledge`, `cmdb_ci`, `cmdb_rel_ci`, `sys_user_group`,
  `sys_choice`, `sys_journal_field`, `sys_user`: read; `incident`, `sc_req_item`, `change_request`: write;
  `incident`, `change_request`: create), the field ones `incident.*`, `change_request.*`, `sys_choice.*`
  (read), `incident.work_notes`, `sc_req_item.work_notes`, `sc_req_item.comments`, `change_request.work_notes`
  (read), `incident.state`, `incident.impact`, `incident.urgency`, `incident.assigned_to`,
  `incident.close_code`, `incident.close_notes`, `incident.comments`, `incident.work_notes`,
  `change_request.state`, `change_request.work_notes` (write), `incident.short_description` (create), and
  the role added to `change_request.*` write (a change's `correlation_id`: a field's create right is its
  write right, through the `*.*` create ACL), `task.state` and `task.work_notes` write (a request item's
  state and close note: `sc_req_item` inherits them from `task`).
- A creation's `correlation_id` is the server's idempotency key: when the role cannot write it, ServiceNow
  files the record without it, and the server's answer says `idempotent: false` with the field to grant.

A user with `itil` instead of the role (which this procedure does not advise) was run too: incidents work,
but `sys_choice` refuses it (the server then uses ServiceNow's out-of-box states), `sys_journal_field`
answers it no row (the server then reads the record's own journal fields), and knowledge articles are not
returned at all.

In Tranibor, the server's descriptor is the floor: reads are allowed; work notes and field updates go
through approval; a customer comment, a resolution and a change request always wait for a person, and an
organisation can only make that stricter.

## In Tranibor

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
4. Approve the triage in Tranibor: a work note signed `[choregos:…]` appears on the incident.

## When it fails

- `401` from the instance: wrong client ID or secret, the client credentials grant is not enabled, or the
  OAuth Application User is empty. With `basic`, check **Web service access only** and the password.
- A read that answers nothing, or `403 Insufficient rights to query records — Field(s) present in the
  query do not have permission to be read`: the role lacks the table's read ACL (ServiceNow then filters
  every row out and answers 200) or a field's (see Permissions).
- `ServiceNow did not apply <field> (asked …, kept …)`: ServiceNow answered 200 to the write and kept the
  old value, which it does for a field the role cannot write and for a value the field refuses (a category
  that does not exist). The server writes back the fields of the same call that were applied.
- `403 Operation Failed — Operation against file 'change_request' was aborted by Business Rule 'Change
  Model: Check State Transition'`: a change moves only along its change model's transitions; the tracker
  cannot move a change from `New` straight to another state through the Table API.
- The webhook answers `401`: the `X-Choregos-Secret` sent differs from `webhook_secret`, or a property is
  empty (the system log shows `Tranibor: … is not set`).
- No outbound request in the log: the Business Rule's filter does not match the incident's assignment
  group, or the rule is inactive.
- A timeout from Tranibor: the egress rule of the plugin's server must allow the instance's host.
- The VIP flag is always false: the role cannot read `sys_user.vip`.
