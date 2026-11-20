---
id: connector.mcp.purchasing
title: Connect the purchasing server
applies_to: {connector: mcp/purchasing}
audience: org_admin
version: 1
reviewed: 2026-11-20
vendor_docs: []
egress: []
values: []
fields:
  backend: "odoo, the only backend the configuration offers; this version of the server cannot start on it yet"
  base_url: "https://<your purchasing system>, without a trailing slash"
  database: "the database name, where the system hosts several"
  username: "the dedicated integration user"
  api_key: "its API key, typed once"
---

## Before you start

- **This version connects nothing real.** The `procurement` plugin's server `purchasing` serves a fixed
  profile of eleven operations (suppliers, framework agreements, contracts, purchase requests), but the
  only backend it runs today is a seeded, in-memory fake of its own: invented suppliers, contracts and one
  draft purchase request, reset when the server restarts. The first real backend, Odoo, arrives with a later
  version of the plugin (PA-06), and this procedure then gains its set-up. Until then, selecting
  `backend: odoo` makes the server refuse to start, and says why in its log; nothing is half-connected.
- The fake is not a setting. An administrator cannot switch to it by typing a field: the platform runs it
  only for its own evaluations (the descriptor's `fake`), as a separate instance with no configuration and no
  network.
- The `procurement` plugin installed in your organisation (Catalogue, **Plugins**). Its server runs in the
  platform; there is nothing to host. A plugin that brings a server needs a second person's approval.
- No bank detail is ever read or written by this server, whatever the backend: payment data stays a human
  process in your purchasing system.

## At the purchasing system

Nothing to set up in this version: the fake needs no account. The backend's own steps (a dedicated
integration user, the least rights that serve the profile, an API key) are written here when the Odoo backend
ships.

## Permissions

The profile's reads (`search_suppliers`, `get_supplier`, `list_framework_agreements`, `list_contracts`,
`get_contract`) are allowed by default. Its six writes (`create_supplier`, `update_supplier`,
`archive_supplier`, `add_contract_note`, `create_purchase_request`, `cancel_purchase_request`) always wait
for a person: the descriptor's `human_approval` is a floor that an organisation can raise and never lower.
Each write carries the platform's `action_key`, so the same governed action replayed writes once. A
supplier or purchase request created by mistake is undone by its compensation (archive, cancel); an update is
undone by writing the previous values back.

## In Tranibor

1. Catalogue, **Plugins**, `procurement`, **Configure**.
2. The server is configured only with the fields of its schema (`backend`, `base_url`, `username`,
   `api_key`, and `database` where the system needs one). While the real backend is not shipped, the
   server cannot be started from them: do not configure it in production.
3. A project uses the connector by its name, `purchasing`, and the operations of the profile; no workflow
   changes when a backend does.

## Test it

There is no administrator test against a real system yet. The plugin's own tests run every operation through
MCP on the fake, in each of its scenes (`default`, `duplicate-supplier`, `expiring-contracts`).

## When it fails

- The server does not start and its log says `PURCHASING_BACKEND=odoo is not served by this version`: this is
  the expected answer today. The Odoo backend arrives with a later version of the plugin.
- A call answers `refused: …`: the call broke a rule of the profile (a bank detail, a malformed date, a
  missing `action_key`). Nothing reached the purchasing system.
- A call answers `conflict: duplicate_supplier …`: an active supplier already has that identity. Use it, or
  update it, instead of creating another.
