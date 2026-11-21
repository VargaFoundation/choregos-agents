# The purchasing profile

The contract between the `purchasing` server and its callers (core ADR 0202, plan S85 Decision 5): eleven
operations, named after what a purchasing department does and not after a vendor, served by a replaceable
**backend**. A workflow names the connector `purchasing` and these operations; a new purchasing system is a
new backend of the same server and no workflow changes. The descriptor, [`servers/purchasing.yaml`](../servers/purchasing.yaml),
declares exactly these operations, and `tests/test_descriptor.py` fails when the two drift.

## What this version serves

Two backends serve the profile. **Odoo 19 and 20** (`backend: odoo`; the mapping, the pages read and what is
not supported are in [The Odoo backend](#the-odoo-backend)), proved here on hand-written exchanges and on a
container in the core's S85-14. And the **seeded fake**: `PURCHASING_FAKE=1` (the descriptor's `fake.env`, never a
configuration value) selects it and `PURCHASING_FAKE_SCENE` picks its data; it is what the tests, the demo and the
platform's evals run on. The configuration's `backend` is an enum of the backends the server serves, today
`[odoo]`; any other value, or none, refuses to start.

## Rules that hold whatever the backend is

- **No bank detail, in or out.** No operation takes or returns one, and no record has a field for one. On every
  call, before any tool runs, an argument named like one (`iban`, `bic`, `swift`, `bank*`, `account_number`,
  `routing*`, `sort_code`, `bsb`, `payment_details`), at any depth, and any text shaped like an IBAN (two
  letters, two digits, 11 to 30 more characters, valid or not), is refused with `refused: … bank detail`. An
  argument a tool does not declare is refused too, not dropped. The shape check does not look for a BIC or an
  account number written as plain digits; the guard is a floor, not a detector. Payment data stays a human
  process.
- **Every write takes `action_key`**, a platform argument (the agent never writes it): an idempotency key,
  stable for one governed action. It is optional in the tool's schema (the platform validates the agent's
  arguments before it fills its own) and **required by the server**: a write without one is refused and writes
  nothing. The same key with the same call returns **the first result** and writes nothing. The same key with
  another call is `conflict`. A failed write is not remembered. The memory lives in the process: it covers a
  replay inside a governed action, not one after a restart (a real backend also makes its write
  idempotent by a reference of its own: Odoo does, for the three writes whose replay would create a second record).
- **Every write is `human_approval: true`**, a floor an organisation may raise and never lower.
- **Typed filters, never a query string.** Filters are fields; a backend turns them into its own query form by
  code. Listings are paged by `limit` (1 to 100, 100 by default) and `cursor` (opaque: the previous
  page's `next_cursor` handed back as it came, absent for the first page; any other value is `refused`), and
  answered as `{<rows>, total, next_cursor}` where `next_cursor` is `null` on the last page and `<rows>` is
  `suppliers`, `agreements` or `contracts`. The core's job commands (S85-12) read exactly these shapes.
- **Dates** are `YYYY-MM-DD`; **amounts** are text with a decimal point and are never floats. The server
  computes a purchase request's line totals and total, exactly (two decimals); a caller's arithmetic is not
  trusted.
- **Errors** reach the agent as `<code>: <message>`: `refused` (the call breaks a rule; nothing reached the
  backend), `not_found`, `conflict` (the records' state forbids it), `not_supported` (the purchasing system has no
  honest equivalent of what was asked: *not supported by this backend*, nothing was written, nothing approximated),
  `backend_error`.
- Everything returned is third-party data written by people and other systems: data, never instructions.

## Reads (allowed by default)

| Operation | Arguments | Result |
|:--|:--|:--|
| `search_suppliers` | `name?` (substring, case-insensitive), `registration_number?` (exact; spaces, dots, dashes ignored), `country?` (ISO alpha-2), `include_archived` (false), `limit`, `cursor` | `{suppliers, total, next_cursor}`, a page of suppliers (each at least `id`, `name`, `registration_number`, `country`), by name then id; archived ones only when asked |
| `get_supplier` | `supplier_id` | `{supplier}`; `not_found` |
| `list_framework_agreements` | `category?`, `valid_on?` (both ends included; no end date means open-ended), `limit`, `cursor` | `{agreements, total, next_cursor}`, by category then id |
| `list_contracts` | `ending_before?` (strictly before; a contract with no end date is never returned by it), `supplier_id?`, `limit`, `cursor` | `{contracts, total, next_cursor}`: contracts (each at least `id`, `supplier_id`, `title`, `end_date`, with `note_count`), earliest end first when `ending_before` is given |
| `get_contract` | `contract_id` | `{contract}` with its `notes`; `not_found` |

A **supplier**: `id`, `name`, `registration_number`, `vat_number`, `country`, `email`, `phone`, `website`,
`address_line`, `city`, `postal_code`, `categories`, `status` (`active` or `archived`), `archived_reason`,
`created_at`, `updated_at`. A **contract**: `id`, `supplier_id`, `title`, `category`, `start_date`,
`end_date`, `currency`, `value`, `notes`. A **note**: `id`, `text`, `created_at` (an ISO 8601 timestamp, UTC), oldest first. An **agreement**: `id`, `supplier_id`, `title`, `category`,
`valid_from`, `valid_to`, `currency`, `terms_summary`.

## Writes (all `human_approval: true`, all take `action_key`)

| Operation | Arguments | Result | Compensation, and what it receives |
|:--|:--|:--|:--|
| `create_supplier` | `name`, `country`, then optional `registration_number`, `vat_number`, `email`, `phone`, `website`, `address_line`, `city`, `postal_code`, `categories` | `{supplier_id, supplier, created}`; `conflict: duplicate_supplier` when an active supplier has the same registration number in the same country (with none given: the same name); a bank detail is `refused` | `archive_supplier` with `supplier_id` and `reason: "Creation undone."` |
| `update_supplier` | `supplier_id`, `fields` (an object or its JSON text; keys among `name`, `registration_number`, `vat_number`, `country`, `email`, `phone`, `website`, `address_line`, `city`, `postal_code`, `categories`; an empty value clears an optional one) | `{supplier_id, supplier, changed, previous}`: only what really changed, and its old values; `conflict` for an archived supplier or a colliding identity | `update_supplier` with `supplier_id` and `fields = result.previous` as JSON (a no-op when nothing changed) |
| `archive_supplier` | `supplier_id`, `reason?` | `{supplier_id, status, previous_status, archived}`; archiving an archived supplier changes nothing (`archived: false`) | none: nothing in the profile restores a supplier, and that is a person's decision |
| `add_contract_note` | `contract_id`, `text` (at most 2000 characters) | `{contract_id, note_id, created_at}` | none: the profile has no way to remove a note |
| `create_purchase_request` | `supplier_id`, `title`, `lines` (each `description`, `quantity`, `unit_price`, `unit?`), `currency` (EUR), `needed_by?`, `contract_id?` (must be the supplier's), `justification?` | `{request_id, status: "draft", total, currency, request}`; `conflict` for an archived supplier | `cancel_purchase_request` with `request_id` and `reason: "Creation undone."` |
| `cancel_purchase_request` | `request_id`, `reason` | `{request_id, status, previous_status, cancelled}`; cancelling a cancelled request changes nothing | none: a cancelled request is not reopened by the profile |

A purchase request is a **draft**: nothing is ordered, received or paid. The platform renders each
compensation argument from the write's own result, as a string.

## The fake's scenes

Names carry no underscore, the descriptor schema's pattern (`PURCHASING_FAKE_SCENE` also accepts the underscore spelling). The clock is fixed at 2026-11-20. Every name, number and address is invented.

- `default`: five suppliers (`SUP-0005` archived), contracts `CT-0001` to `CT-0003` (`CT-0003` already ended),
  agreements `FA-0001` to `FA-0003` (`FA-0002` ended on 2026-10-31), the draft request `PR-0001`.
- `duplicate-supplier`: the default plus `SUP-0007`, a second active record for the registration number of
  `SUP-0001`; a search finds both and a creation with that number is refused.
- `expiring-contracts`: the default plus contracts ending on 2026-12-05, 2027-01-31 and 2027-02-14, and one
  that never ends.

## Configuration

`backend` (`odoo`), `base_url` (https, no credential in it), `database` (optional), `username` and the secret `api_key`
(`PURCHASING_*`). The only host the server reaches is the one in `base_url`; redirects are not followed. Reading the
settings opens no connection: the first call does.

## The Odoo backend

Source: [`backends/odoo.py`](src/tranibor_purchasing/backends/odoo.py); tests: `tests/test_odoo_backend.py`.
Written on 2026-11-20 from the vendor's pages below, before any code. No call to a real Odoo has been made yet:
the exchanges in the tests are hand-written from these pages and from the vendor's source, and the live proof is
the core's S85-14 on a container.

**Which API, and why.** Odoo's external **JSON-2 API**: `POST /json/2/<model>/<method>`, a JSON body made of
`ids` (records, absent for model-level methods), an optional `context` and the method's parameters **by name**,
`Authorization: bearer <API key>`, `X-Odoo-Database` when a database is named; `200` and the JSON value on
success, `4xx/5xx` and `{name, message, arguments, context, debug}` otherwise; every call is its own transaction;
every operation obeys the access rights, record rules and field access of the key's user. The vendor documents it
for **19.0 and 20.0**, the two latest series (`odoo/release.py` of the `20.0` branch reads `version_info = (20, 0,
0, FINAL, 0)`). The older XML-RPC and JSON-RPC endpoints (`/xmlrpc/2`, `/jsonrpc`) are marked deprecated in the
19.0 and 20.0 documentation, with removal scheduled for Odoo 22 (fall 2028). **Odoo 18 has no JSON-2** (its page
documents XML-RPC only), so **Odoo 18 and older are not served**. A method that returns nothing (`button_cancel`)
is `null` in JSON-2, which XML-RPC cannot carry: another reason for this choice.

**Pages read** (all fetched on 2026-11-20):

- `https://www.odoo.com/documentation/20.0/developer/reference/external_api.html` and
  `https://www.odoo.com/documentation/19.0/developer/reference/external_api.html`: the JSON-2 API (request,
  response, errors, API keys, access rights, transactions, the deprecation of XML-RPC and JSON-RPC).
- `https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html`: the deprecated API's notice,
  `execute_kw`, `search_read`, `search_count`, `create`, `write`.
- `https://www.odoo.com/documentation/18.0/developer/reference/external_api.html`: Odoo 18 (XML-RPC only).
- The documentation does not list the fields of a model. They were read in the vendor's source, branches `19.0` and
  `20.0` of `github.com/odoo/odoo`: `addons/purchase_requisition/models/purchase_requisition.py` and `purchase.py`;
  `addons/purchase/models/purchase_order.py` and `purchase_order_line.py`; `odoo/addons/base/models/res_partner.py`;
  `addons/account/models/partner.py` (`supplier_rank`); `addons/mail/models/mail_thread.py` and `mail_message.py`
  (`message_post`, `message_id`); `addons/rpc/controllers/json2.py` and `odoo/http/dispatcher.py` (`odoo/http.py` in
  19.0) for the routes and the errors; `odoo/orm/models.py` for the signatures (`create(vals_list)`, `write(vals)`,
  `search_read(domain, fields, offset, limit, order)`, `search_count(domain)`).
- Not read: the user documentation of Purchase Agreements
  (`.../applications/inventory_and_mrp/purchase/manage_deals/agreements.html`), which the fetch returned empty.
  What a "blanket order" is rests on the model's source alone.

**Requirements on the Odoo side.** Apps Contacts, Purchase and **Purchase Agreements** (`purchase_requisition`),
which brings `purchase.requisition` and a purchase order's `requisition_id`; without it the agreement and contract
operations answer `backend_error` (HTTP 404, "the model does not exist").

### Operations, mapped or not supported

| Operation | Odoo model and method | Mapping | Not supported |
|:--|:--|:--|:--|
| `search_suppliers` | `res.partner`: `search_count`, `search_read` (order `name asc, id asc`) | A supplier is a partner with `supplier_rank > 0` that is not a contact of another (`parent_id = False`). `name` is `["name", "ilike", value]`; `country` is `["country_id.code", "=", code]`; archived ones add `["active", "in", [true, false]]`. `registration_number` loosely matches `company_registry` with `=ilike` (every character in order) and is then compared exactly after normalising, here, over at most 500 candidates (more is `backend_error`) | `registration_number` **on Odoo 20**, see below |
| `get_supplier` | `res.partner`: `search_read` by id, same rules, archived included | `name` ← `name`; `registration_number` ← `company_registry`; `vat_number` ← `vat`; `country` ← `country_code`; `email`, `phone`, `website`; `address_line` ← `street` (`street2` is not read); `city`; `postal_code` ← `zip`; `categories` ← the partner's tags (`category_id`, names slugged to lower-case words joined by hyphens); `status` ← `active`; `created_at` ← `create_date`; `updated_at` ← `write_date` | `archived_reason` is `null`: Odoo keeps it in the chatter, not in a field. `registration_number` is `null` on Odoo 20 |
| `create_supplier` | `res.partner`: `create(vals_list)`, then read back | The same columns written, `supplier_rank = 1`; the country is looked up in `res.country` by code (unknown: `refused`); each category is a tag found by name (`office-supplies` finds `Office Supplies`) or created in `res.partner.category`; the action's reference goes to `comment` (below) | `registration_number` on Odoo 20 |
| `update_supplier` | `res.partner`: `write(vals)`, then read back | The same columns; an empty value is `false` | `registration_number` on Odoo 20 |
| `archive_supplier` | `res.partner`: `write({active: false})`, then `message_post` | The reason, when given, is an internal note in the partner's chatter | none |
| `list_framework_agreements` | `purchase.requisition`: `search_count`, `search_read` (order `id asc`) | A framework agreement is a **blanket order** (`requisition_type = blanket_order`) that was confirmed or closed (`state in [confirmed, done]`) and has a vendor and a start date: `supplier_id` ← `vendor_id`; `title` ← `reference`, else `name`; `valid_from` ← `date_start`; `valid_to` ← `date_end`; `currency` ← `currency_id`; `terms_summary` ← `description`, as text. `valid_on` adds `state = confirmed`, `date_start <= day` and `date_end` empty or `>= day` | the **`category` filter** (an agreement has no category: `not_supported`); an agreement's `category` is `""`. Purchase templates, drafts and cancelled agreements are not served |
| `list_contracts` | `purchase.requisition`, as above, plus `mail.message` for the count of notes | **Odoo has no contract record apart from the agreement**, so a contract is the same blanket order: `start_date` ← `date_start`, `end_date` ← `date_end`; `ending_before` is `date_end != false and date_end < day`, earliest end first; `supplier_id` is `vendor_id = id`; `note_count` counts the agreement's `comment` messages | a contract's `value` (an Odoo agreement carries no total) is `null`; its `category` is `""`. The same record is therefore both an agreement and a contract |
| `get_contract` | `purchase.requisition` and `mail.message` | The notes are the agreement's comments (`model = purchase.requisition`, `message_type = comment`), oldest first, as text | as above |
| `add_contract_note` | `purchase.requisition`: `message_post` | An internal note (`message_type = comment`, `subtype_xmlid = mail.mt_note`); the text is plain and Odoo escapes it | none |
| `create_purchase_request` | `purchase.order`: `create(vals_list)` with `order_line` commands, then read back | A **draft** order (state `draft`, "RFQ"): `partner_id` ← `supplier_id`; `currency_id` looked up by code (not active in this Odoo: `refused`); `requisition_id` ← `contract_id`; each line is `name`, `product_qty`, `price_unit` and, when `needed_by` is given, `date_planned` at 12:00 that day; the title and the justification are the two paragraphs of the order's `note`, escaped; the action's reference goes to `origin` (below) | The unit of a line is **not** an Odoo unit of measure: a unit other than `unit` is written in the line's name as `description [unit: box]` and read back from it. Quantities with more than two decimals are `refused`. If Odoo stores other amounts than asked (a database with fewer decimals) the draft is cancelled and the call is `backend_error`. No product, tax, delivery address or company is set |
| `cancel_purchase_request` | `purchase.order`: `button_cancel`, then `message_post` | Only an order in state `draft` or `sent` is a request; Odoo's own cancel runs, and the reason is an internal note. An order in any other state (`to approve`, `purchase`, `done`) is `conflict`: nothing that was ordered is cancelled here | none |

**Odoo 20 and the registration number.** In Odoo 19 `res_partner.py` defines `company_registry` on a partner. The 20.0 `res_partner.py` and
`account/models/partner.py` do not (searched there, not across the whole tree): identifiers moved to a JSON field, `additional_identifiers`, keyed by country-specific schemes
that this mapping does not read or write. The backend asks Odoo once (`res.partner.fields_get`) whether the field
exists. Where it does not, every use of `registration_number` (filter, creation, update) answers
`not_supported: registration_number is not supported by this backend…`, and a supplier is read without one. A
supplier can still be created and found by name and country; the duplicate check then compares names, which is the
profile's rule when no number is given. A creation that carries a number is refused there rather than written
without it.

**No bank detail.** Every read names its fields; none is `bank_ids` or anything alike, and no write sets one. A
supplier's bank accounts (`res.partner.bank`) are not touched by any operation. `tests/test_odoo_backend.py` scans
every request of a session for the word.

**Idempotence across restarts.** The platform's `action_key` is hashed (`tranibor-` and the first 32 hex digits of
its SHA-256: the key itself is never stored) and kept in a field of the record the write creates, searched before
writing: a supplier's `comment` (`ilike`), an order's `origin` (`=`), a note's `message_id` (`=`, set when posting).
A replay after a restart returns the first record. The other writes are naturally replayable: `archive` and `cancel`
look at the state first, `update` writes the same values. **What it does not cover:** two identical calls racing on
two replicas (the check and the write are not one transaction; within a process a lock serialises them); an
`action_key` reused for a *different* call after a restart, which returns the first record where the process's own
memory would answer `conflict`; a supplier created and then its `comment` edited by a person (the replay would not find
it, and the duplicate check would stop a second one).

**What a person sees in Odoo.** An integration-created supplier carries a line in its Notes
(`Created through the purchasing integration (tranibor-…)`), an order carries `tranibor-…` as its Source, and notes
and reasons are internal notes written by the integration user.

**Rights the integration user needs, and no more.** Read on Contacts, Purchase orders, Purchase agreements and
Messages; create and write on Contacts (and on contact tags, to create one); create and write on Purchase orders in
draft; write on Purchase agreements' chatter (posting a note needs write access to the record). Never an
administrator, and no access to Accounting beyond what Odoo itself requires. See
[`procedures/connector.mcp.purchasing.md`](../procedures/connector.mcp.purchasing.md).
