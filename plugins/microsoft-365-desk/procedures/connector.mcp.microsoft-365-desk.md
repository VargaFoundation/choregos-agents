---
id: connector.mcp.microsoft-365-desk
title: Connect an IT desk's shared mailbox and Teams channel
applies_to: {connector: mcp/microsoft-365-desk}
audience: org_admin
version: 1
reviewed: 2026-11-20
vendor_docs:
  - {title: "Register an application in Microsoft Entra ID", url: "https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app"}
  - {title: "Add and manage application credentials", url: "https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-credentials"}
  - {title: "Role Based Access Control for Applications in Exchange Online", url: "https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac"}
  - {title: "Limiting application permissions to specific Exchange Online mailboxes", url: "https://learn.microsoft.com/en-us/graph/auth-limit-mailbox-access"}
  - {title: "Microsoft Graph permissions reference", url: "https://learn.microsoft.com/en-us/graph/permissions-reference"}
  - {title: "Obtain immutable identifiers for Outlook resources", url: "https://learn.microsoft.com/en-us/graph/outlook-immutable-id"}
  - {title: "Create an Azure Bot resource", url: "https://learn.microsoft.com/en-us/azure/bot-service/abs-quickstart"}
  - {title: "Authentication with the Bot Connector API", url: "https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication"}
  - {title: "Resource-specific consent for Teams apps", url: "https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/resource-specific-consent"}
  - {title: "Teams app manifest schema", url: "https://learn.microsoft.com/en-us/microsoftteams/platform/resources/schema/manifest-schema"}
egress: ["graph.microsoft.com", "login.microsoftonline.com", "login.botframework.com", "smba.trafficmanager.net"]
values: ["plugin_webhook_url:microsoft-365-desk/desk"]
fields:
  tenant_id: "the Directory (tenant) ID of your Entra tenant"
  client_id: "the Application (client) ID of the application registered below (also the bot's Microsoft App ID)"
  client_secret: "a client secret of that application, typed once"
  mailbox: "the shared mailbox's address, e.g. it-desk@contoso.com; leave empty for Teams only"
  teams: "the channels the bot answers in, each {team_id, channel_id}; leave empty for mail only"
  bot_app_id: "empty, unless the bot is another application than client_id"
  bot_tenant_id: "your tenant id for a single-tenant bot (the default for new bots); botframework.com for a multi-tenant one"
  service_url: "leave the default, https://smba.trafficmanager.net/teams/"
  start_label: "the start label of the project that takes the requests, agent-ready by default"
---

## Before you start

- A Microsoft 365 tenant where you can register an application, someone who can grant admin consent, an
  Exchange administrator (for the mailbox scope), and, for Teams, an Azure subscription (the bot
  resource) and the owner of the team.
- The `microsoft-365-desk` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs
  in the platform; there is nothing to host. A plugin that brings a server needs a second person's
  approval.
- A Tranibor core 0.28.0 or later: it forwards the bot's signed token to the server (ADR 0198).
- The desk's shared mailbox (for instance `it-desk@contoso.com`), and/or the Teams channel requests are
  asked in. Each channel is optional; at least one is configured.
- What the server answers: the requester, in the original thread (a reply to the mail, or a reply in the
  Teams thread). It never reads attachments, never writes to another mailbox, and never posts outside the
  thread of a request.
- This procedure is written from Microsoft's documentation and the plugin's fake tenant; it has not yet
  been run end to end against a live tenant.

## At Microsoft

**The application (Entra admin center).**

1. Identity, Applications, App registrations, **New registration**: name `tranibor-desk`, **Accounts in
   this organizational directory only**, no redirect URI. Copy the **Application (client) ID** and the
   **Directory (tenant) ID**.
2. API permissions: remove `User.Read`. The server reads no profile.
3. Certificates & secrets, **New client secret**, with an expiry your policy allows (note the date: the
   desk stops when it expires). Copy its **Value** once into your secret manager.

**The shared mailbox, and no other (Exchange Online PowerShell).** The application must reach the desk's
mailbox only. Use **RBAC for Applications** (recommended). Its roles are the application's mail rights:
do **not** also grant the Microsoft Graph application permissions `Mail.ReadWrite` or `Mail.Send` in
Entra — an Entra grant is tenant-wide and adds to the scoped roles, so every mailbox would be reachable.

4. Connect with `Connect-ExchangeOnline`, then register the application's service principal in Exchange
   (the Object ID is the one of the application's **Enterprise application**, not of its registration):

   ```powershell
   New-ServicePrincipal -AppId <client id> -ObjectId <enterprise application object id> -DisplayName "Tranibor desk"
   New-ManagementScope -Name "Tranibor desk mailbox" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'it-desk@contoso.com'"
   New-ManagementRoleAssignment -App <client id> -Role "Application Mail.ReadWrite" -CustomResourceScope "Tranibor desk mailbox"
   New-ManagementRoleAssignment -App <client id> -Role "Application Mail.Send" -CustomResourceScope "Tranibor desk mailbox"
   ```

5. Check the scope: the desk's mailbox is in scope, a neighbour's is not.

   ```powershell
   Test-ServicePrincipalAuthorization -Identity <client id> -Resource it-desk@contoso.com
   Test-ServicePrincipalAuthorization -Identity <client id> -Resource someone.else@contoso.com
   ```

   The first answers `InScope: True` for both roles, the second `InScope: False`. Role assignments take
   up to an hour to apply.

   The older way, an **Application Access Policy**, also works: grant `Mail.ReadWrite` and `Mail.Send`
   as Microsoft Graph **application** permissions in Entra with admin consent, put the mailbox in a
   mail-enabled security group, then
   `New-ApplicationAccessPolicy -AppId <client id> -PolicyScopeGroupId <group address> -AccessRight RestrictAccess -Description "Tranibor desk"`
   and check with `Test-ApplicationAccessPolicy -Identity someone.else@contoso.com -AppId <client id>`
   (`Denied`). Microsoft is replacing these policies with RBAC for Applications.

**The bot (Azure portal), for Teams.**

6. Create an **Azure Bot** resource: type of app **Single Tenant**, creation type **Use existing app
   registration**, App ID the client id above, tenant your tenant. (A multi-tenant bot created earlier
   works too; set `bot_tenant_id` to `botframework.com`.)
7. Its Configuration: **Messaging endpoint** `{{plugin_webhook_url:microsoft-365-desk/desk}}`. Channels:
   add **Microsoft Teams**.

**The Teams app, for Teams.**

8. Build the app package from the plugin's repository (`teams/` is not part of the installed plugin):
   `python teams/build_app.py --client-id <client id> --developer "<your organisation>" --website
   https://<your site> -o it-desk.zip`. It fills the manifest's placeholders (refusing an id that is not a
   GUID, a site that is not `https://`, any placeholder left) and adds the two icons Teams asks for,
   `color.png` (192 × 192) and `outline.png` (32 × 32, white on transparent) — generated, unless you put
   your own beside the script. The manifest declares the bot (scope `team`) and one resource-specific permission,
   `ChannelMessage.Read.Group`: the application reads the messages of the teams the app is installed in,
   and nothing else.
9. Teams admin center: allow custom apps for the people who install it, and allow group owners to consent
   to resource-specific permissions (Microsoft Entra, Enterprise applications, Consent and permissions, or
   the Teams RSC settings). Upload the app (Teams apps, Manage apps, **Upload new app**), then the team's
   owner adds it to the team and accepts the permission.
10. The channel's ids: in Teams, the channel's **Get link to channel** gives `groupId=<team id>` and
    `19:…@thread.tacv2` (URL-decoded) as the channel id.

## Permissions

- **Mail, through RBAC for Applications**: `Application Mail.ReadWrite` (read the Inbox, create a reply
  draft, set categories and the plugin's extended properties) and `Application Mail.Send` (send that
  reply), both scoped to the desk's mailbox. No Microsoft Graph mail permission is granted in Entra.
- **Teams**: the resource-specific permission `ChannelMessage.Read.Group` of the Teams app, consented by
  the team's owner: the application reads the channel messages of that team only. The bot answers in a
  thread through the Bot Connector with its own token; it never reads chats nor other teams.
- What the server writes in the mailbox: a reply to the sender (`createReply`, `send`), the categories
  `Choregos: <state>` and `Choregos: Done`, and single-value extended properties (the plugin's property set
  `{793633a1-729c-497c-930e-276450befa92}`) holding the action key of each reply and Tranibor's status.
- In Tranibor, the server's descriptor is the floor: reads are allowed; `reply` is a write and waits for
  approval unless your organisation's policy decides otherwise. The tracker writes (`tracker_*`) are the
  platform's own calls, audited, not approved one by one; `tracker_comment` writes nothing, so the
  platform's own comments never reach the requester.

## In Tranibor

1. Catalogue, **Plugins**, `microsoft-365-desk`, **Configure**.
2. `tenant_id`, `client_id` from step 1; `client_secret` from step 3 (sealed, shown once).
3. `mailbox`: the desk's address. `teams`: one `{team_id, channel_id}` per channel from step 10.
4. `bot_tenant_id`: your tenant id for a single-tenant bot (step 6), `botframework.com` for a multi-tenant
   one. Leave `bot_app_id` empty when the bot is the same application.
5. Leave `service_url` at `https://smba.trafficmanager.net/teams/`: each verified activity names the
   service URL replies go to, and the server uses it; the default serves until the first one arrives. The
   server reaches `smba.trafficmanager.net` only; a sovereign cloud's Bot Connector (another host) needs a
   new version of the plugin, whose declaration lists that host.
6. Make the desk the tracker of a project (`plugin:microsoft-365-desk/desk`), whose start label is
   `start_label`.

## Test it

1. Press **Test** on the connector: the server answers and serves the eight tracker tools and `reply`.
2. Send a mail to the desk's mailbox. Within the polling interval it appears on the project's board;
   in Outlook, the message gets the category `Choregos: …` once Tranibor takes it.
3. Approve a `reply` of the agent: the sender receives an answer in the same conversation (`RE: …`), once,
   even if the action is replayed.
4. In the Teams channel, post a message that mentions the bot. It becomes a work item; the platform's
   webhook log shows the delivery verified.
5. Approve a reply: it appears in the thread, ending with `Ref: desk-…`.
6. Check the scope once more: `Test-ServicePrincipalAuthorization` for a neighbour's mailbox still answers
   `InScope: False`.

## When it fails

- `unauthorized: Entra refused the application's credentials`: a wrong client id or secret, or the secret
  expired.
- `forbidden: Microsoft Graph refused the application (ErrorAccessDenied)` on the mailbox: the RBAC role
  assignments are missing or not applied yet (up to an hour), or `mailbox` is not the scoped mailbox.
- Every Teams delivery answered `401`: the bot's messaging endpoint is not the webhook URL above, the
  bot's App ID is not `client_id` (or `bot_app_id`), or the platform is older than 0.28.0 and drops the
  `Authorization` header.
- A delivery verified but no work item: the channel is not in `teams`, the message does not mention the
  bot, or it mentions it and says nothing else.
- `forbidden` reading a Teams message: the Teams app is not installed in that team, or the owner did not
  consent to `ChannelMessage.Read.Group`.
- A Teams reply refused with `unauthorized` by the Bot Connector: `bot_tenant_id` does not match the bot's
  type (single-tenant: your tenant id; multi-tenant: `botframework.com`).
- A mail skipped by `tracker_list_candidates` (its `reason` says how many): its id is longer than a key
  allows. Answer it by hand.
- A mail that never becomes a work item: an automatic one (an `Auto-Submitted` header, an out-of-office
  reply, a delivery report), or a follow-up in a conversation the desk already took (a message of it is
  categorised `Choregos: …`). Remove the category from the taken message to let the thread be taken again.
- A `reply` refused with `work_item_key`: the action has no work item, or belongs to another request than
  the one it answers; a fan-out child answers its parent's requester only.
