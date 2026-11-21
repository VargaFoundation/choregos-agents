---
id: connector.mcp.purchasing
title: Connect the purchasing server
applies_to: {connector: mcp/purchasing}
audience: org_admin
version: 2
reviewed: 2026-11-20
vendor_docs:
  - {title: "Odoo 20.0: External JSON-2 API", url: "https://www.odoo.com/documentation/20.0/developer/reference/external_api.html"}
  - {title: "Odoo 19.0: External JSON-2 API", url: "https://www.odoo.com/documentation/19.0/developer/reference/external_api.html"}
egress: []
values: []
fields:
  backend: "odoo, the one backend this version serves (Odoo 19 or 20)"
  base_url: "https://<your Odoo>, without a trailing slash"
  database: "the Odoo database name; leave it empty where the host name alone selects the database"
  username: "the dedicated integration user's login (kept for the audit trail; Odoo identifies the user by the API key)"
  api_key: "the integration user's API key, typed once"
---

## Before you start

- **The backend is Odoo 19 or 20**, reached through the vendor's external JSON-2 API with an API key. Odoo 18 and
  older have no JSON-2 and are not served. The API is only available on Odoo's Custom pricing plans; on Odoo
  Online, Odoo's own plan decides.
- In Odoo, install the apps **Contacts**, **Purchase** and **Purchase Agreements** (`purchase_requisition`). Without
  the last one, the agreement and contract operations answer `backend_error` (the model does not exist) and the others
  work.
- What the profile cannot say in Odoo, and what Odoo 20 changes, is written in the plugin's `PROFILE.md`, "The Odoo
  backend". In short: a contract is a blanket order, so it is also an agreement; an agreement has no category and a
  contract no value; a unit of measure is written in the line's name; and **on Odoo 20 a supplier's registration
  number is not supported** (such a creation is refused, nothing is written without it).
- The `procurement` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in the
  platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- No bank detail is ever read or written by this server: payment data stays a human process in Odoo. The
  integration user needs, and should be given, no access to bank accounts.
- The fake is not a setting. An administrator cannot switch to it by typing a field: the platform runs it only for
  its own evaluations (the descriptor's `fake`), as a separate instance with no configuration and no network.

## At the purchasing system

1. **A dedicated user.** Settings, Users, New. A name that says what it is (for example `Purchasing integration`),
   a login (this is the `username` field), **no administrator right**, and an email nobody reads is fine. Never reuse
   a person's account: every note and order the integration writes is attributed to this user, and a person's
   leaving would break the connection.
2. **The least rights that serve the profile**, in the user's Access Rights tab (Odoo names vary a little between
   versions; give the lowest level that works, then run the test below):
   - Purchase: **User**. Raise it to Administrator only if the test below proves it necessary on your Odoo.
   - Contact creation (the "Contact Creation" group, `Creates and edits partners and their tags`): required to
     create or edit a supplier and to create a tag.
   - Purchase Agreements: the level of the Purchase group is enough to read; the user must be able to *write* the
     agreements it adds a note to (a note is a message on the record).
   - Nothing under Accounting, Settings, Technical or Administration. No access to bank accounts.
   - A record rule that limits the user to the company or the buyers it should serve, if you have several.
3. **An API key**, made *by that user* (log in as the user, or use Odoo's own tool for it): Preferences, Account
   Security, **New API Key**, a description (`tranibor purchasing`) and a duration. **A key lasts three months at
   most**: write the renewal in your calendar, and replace the key in Tranibor before it expires, or every call
   answers `backend_error` with `the API key is invalid`. The key is shown once; copy it into Tranibor's `api_key`
   field and nowhere else. Revoke it in the same screen if it leaks.
4. **The database name**: Preferences, or your Odoo URL's database selector. Where one host serves one database
   by its name, leave `database` empty.
5. Nothing is installed in Odoo, and nothing is changed in its configuration. The server writes two things people
   will see: a line in a new supplier's Notes and a Source on a draft order (`tranibor-` and a code), which make
   a replayed write find the first record instead of creating a second.

## Permissions

The profile's reads (`search_suppliers`, `get_supplier`, `list_framework_agreements`, `list_contracts`,
`get_contract`) are allowed by default. Its six writes (`create_supplier`, `update_supplier`,
`archive_supplier`, `add_contract_note`, `create_purchase_request`, `cancel_purchase_request`) always wait
for a person: the descriptor's `human_approval` is a floor that an organisation can raise and never lower.
Each write carries the platform's `action_key`, so the same governed action replayed writes once, also after the
server restarted. A supplier or purchase request created by mistake is undone by its compensation (archive,
cancel); an update is undone by writing the previous values back. A purchase request is a **draft order**
(RFQ): nothing is ordered, and an order that has been confirmed is never cancelled by this server.

## In Tranibor

1. Catalogue, **Plugins**, `procurement`, **Configure**.
2. Fill `backend` (`odoo`), `base_url`, `database` where needed, `username` and `api_key`. The server refuses to
   start on a `base_url` that is not https, that carries a credential, or on a missing user or key, and says which in
   its log; it opens no connection until its first call.
3. A project uses the connector by its name, `purchasing`, and the operations of the profile; no workflow
   changes when a backend does.

## Test it

Ask an agent that holds the connector to call `search_suppliers` with a `name` you know. An answer
with suppliers proves the URL, the database, the key and the read rights. Then, with a person's approval,
`create_supplier` for an invented supplier and `archive_supplier` for it: that proves the write rights and
the chatter. Last, `create_purchase_request` to it with one line and `cancel_purchase_request`: that proves
the order rights. Archive or cancel what you created; nothing here deletes.

## When it fails

- `backend_error: odoo refused the call (401)… the API key is invalid, expired…`: the key is wrong, revoked or
  past its three months, or the database is not the one it was made in. Make a new key (step 3).
- `backend_error: odoo refused the call (403)… lacks the right for <model>.<method>`: the integration user lacks a
  right; the model and method are named. Add the lowest right that serves it (step 2) and try again.
- `backend_error: odoo answered 404 … the model … does not exist`: **Purchase Agreements** is not installed.
- `backend_error: odoo could not be reached` or `did not answer … in time`: the URL, the network or the Odoo itself.
  The server reaches only the host in `base_url` and follows no redirect: an Odoo that redirects (to another host
  or to a login page) is a configuration to fix at the Odoo, not here.
- `not_supported: …`: the profile asked for what Odoo has no honest equivalent of (a category filter on
  agreements; a registration number on Odoo 20). Nothing was written.
- A call answers `refused: …`: the call broke a rule of the profile (a bank detail, a malformed date, a missing
  `action_key`, a country or currency this Odoo does not have). Nothing reached Odoo, or nothing was written.
- A call answers `conflict: duplicate_supplier …`: an active supplier already has that identity. Use it, or
  update it, instead of creating another. `conflict` on a purchase request means its order is no longer a draft.
