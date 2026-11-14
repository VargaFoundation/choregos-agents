---
id: connector.mcp.microsoft-365
title: Connect Microsoft 365, read as each person
applies_to: {connector: mcp/microsoft-365}
audience: org_admin
version: 1
reviewed: 2026-11-14
vendor_docs:
  - {title: "Register an application in Microsoft Entra ID", url: "https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app"}
  - {title: "Add and manage application credentials", url: "https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-credentials"}
  - {title: "Grant tenant-wide admin consent to an application", url: "https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent"}
  - {title: "Microsoft Graph permissions reference", url: "https://learn.microsoft.com/en-us/graph/permissions-reference"}
  - {title: "OAuth 2.0 authorization code flow", url: "https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow"}
  - {title: "az ad app", url: "https://learn.microsoft.com/en-us/cli/azure/ad/app#az-ad-app-show"}
  - {title: "user: revokeSignInSessions", url: "https://learn.microsoft.com/en-us/graph/api/user-revokesigninsessions"}
egress: ["graph.microsoft.com", "login.microsoftonline.com"]
values: ["api_url", "public_url", "org"]
fields:
  identity_modes: "[user_consent]: each person connects their own account; the server has no identity of its own"
  user_consent: "the tenant's authorize and token endpoints, the application's client id, the client secret's reference, the scopes of the stage"
---

## Before you start

- A Microsoft Entra tenant where you can register an application, and someone who can grant tenant-wide
  admin consent (a Privileged Role Administrator, Cloud Application Administrator or Application
  Administrator).
- The `microsoft-365` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in
  the platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- A platform administrator of this Choregos deployment: the application's client secret is read from the
  deployment's environment (`env:`), which only they write.
- Decide the stage. **Stage 2** reads the calendar only (`list_events_with`). **Stage 3** adds the mail
  and Teams chats a person shares with another (`search_mail_with`, `search_chats_with`). Start with stage
  2; moving to stage 3 is a new consent, by the tenant and then by each person.
- This procedure is written from Microsoft's documentation and the plugin's fake tenant; it has not yet
  been run end to end against a live tenant.

## At Microsoft Entra

1. **Register the application.** Entra admin center, Identity, Applications, App registrations, **New
   registration**: name `choregos-microsoft-365`, supported account types **Accounts in this
   organizational directory only** (single tenant). Redirect URI: platform **Web**,
   `{{api_url}}/api/v1/me/connected-accounts/callback`. Register.
2. **Copy two ids** from its Overview: the Application (client) ID and the Directory (tenant) ID.
3. **Remove what Entra adds by default.** API permissions: remove `User.Read`. The server never reads a
   profile; the platform asks Graph for the scopes below only.
4. **Add the stage's delegated permissions.** API permissions, **Add a permission**, Microsoft Graph,
   **Delegated permissions**: `offline_access` and `Calendars.Read` (stage 2); add `Mail.Read` and
   `Chat.Read` for stage 3. Never an application permission: the server reads only as a person.
5. **Grant admin consent** for the tenant (**Grant admin consent for <tenant>**), so that each person is
   not asked to consent on their own. Every permission shows *Granted*.
6. **A client secret.** Certificates & secrets, **New client secret**, an expiry your policy allows (note
   the date: Choregos cannot read Graph for anyone once it expires). Copy its **Value** once and hand it to
   the platform administrator through your secret manager, never by mail or chat.
7. **Authentication.** Leave implicit grant off (no access tokens, no ID tokens) and **Allow public client
   flows** off.
8. **Check it automatically.** With the Azure CLI, export the application as Entra holds it and run the
   plugin's check for your stage, from the plugin's `server/` directory:
   `az ad app show --id <client id> > app.json`, then
   `uv run python -m choregos_microsoft_365.entra --stage 2 app.json`. It prints `ok`, or each permission,
   redirect URI or setting that breaks the stage's rules. The plugin's `server/entra/stage-2.json` and
   `stage-3.json` are the reference applications.

## Permissions

- Delegated only: Choregos reads Microsoft 365 as the person who asks, never with an identity of its own,
  so Microsoft 365 decides what that person may read. A call without the person's connected account is
  refused before anything reaches Graph.
- Stage 2: `Calendars.Read` (their calendar) and `offline_access` (a refresh token, sealed in the person's
  own space in Choregos). Stage 3 adds `Mail.Read` (their mailbox) and `Chat.Read` (their Teams chats).
- Nothing is written to Microsoft 365. The server stores nothing; it returns at most 20 items, 300-character
  snippets, never a full body nor an attachment, and only mail and chats the other person takes part in.
- The client secret stays with the platform, which redeems each person's code and refreshes their token;
  the server never receives it.

## In Choregos

1. **The client secret (platform administrator).** Put the secret's value in the API's environment as
   `CONNECTOR_MICROSOFT_365_CLIENT_SECRET` (from your secret manager, as for any `env:` reference of the
   deployment), and allow `login.microsoftonline.com` and `graph.microsoft.com` in the organisation's
   network bounds.
2. **Declare the consent on the connector.** The plugin's install created the connector `microsoft-365`.
   Set its configuration (Connectors, `microsoft-365`, or `PATCH {{api_url}}/api/v1/orgs/{{org}}/connectors/microsoft-365`
   with a `config` body) to:

   ```yaml
   identity_modes: [user_consent]
   user_consent:
     authorization_endpoint: https://login.microsoftonline.com/<tenant id>/oauth2/v2.0/authorize
     token_endpoint: https://login.microsoftonline.com/<tenant id>/oauth2/v2.0/token
     client_id: <client id>
     client_secret_ref: env:CONNECTOR_MICROSOFT_365_CLIENT_SECRET
     scopes: [offline_access, Calendars.Read]          # stage 3: add Mail.Read and Chat.Read
   ```

   The server's schema refuses another mode, a multi-tenant endpoint (`common`, `organizations`) and any
   other scope. Microsoft offers no revocation endpoint for these tokens: leave none.
3. **Bind it.** An assistant that needs Microsoft 365 binds its need to this connector with the
   `user_consent` identity (the installation's bindings). Its tools are `<need>__list_events_with`, and at
   stage 3 `<need>__search_mail_with` and `<need>__search_chats_with`.
4. **Each person connects their account** once, at `{{public_url}}/me/connected-accounts`: **Connect**
   next to `microsoft-365`, then Microsoft's sign-in. Until they do, a tool answers
   `account_not_connected` with that page's address, and the assistant goes on without the source.

## Test it

1. As a person who connected their account, ask an assistant bound to the connector for your meetings of
   the next 24 hours: it calls `list_events_with` with `within_hours: 24` and lists them, each with its
   `as_of`.
2. Ask for your one-to-one meetings with a colleague: `one_to_one_only` leaves out a meeting of three.
3. Stage 3: ask for your recent mail with that colleague. Only threads they sent or received come back,
   each with a snippet, never a body nor an attachment.
4. Disconnect your account at `{{public_url}}/me/connected-accounts`: the next call answers
   `account_not_connected` and nothing reaches Microsoft.

## When it fails

- **`account_not_connected`**: the person has not connected their account, or disconnected it, or the
  connector's consent settings changed since they connected (Choregos then asks them to connect again).
- **`identity_unavailable`**: no person's token came with the call (the need is not bound `user_consent`),
  or Microsoft refused it: expired, revoked, or the client secret expired. Renew the secret, then each
  person connects again.
- **`forbidden`**: the tenant has not consented to the permission the tool needs (`search_mail_with` at
  stage 2, for instance), or a Conditional Access policy refuses the platform. Grant the stage's consent
  again; check the sign-in logs of the application.
- **AADSTS50011** at sign-in: the redirect URI is not exactly
  `{{api_url}}/api/v1/me/connected-accounts/callback`.
- **AADSTS65001** at sign-in: admin consent was not granted, or a scope asked is not on the application.
- **`rate_limited`**: Microsoft throttled the calls; the message says when to retry.
- **To cut every access of a person at once**, beyond disconnecting their account: revoke their sessions in
  Entra (`revokeSignInSessions`), which invalidates the refresh token Choregos holds.
