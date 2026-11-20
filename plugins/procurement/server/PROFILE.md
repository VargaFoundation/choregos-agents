# The purchasing profile

The contract between the `purchasing` server and its callers (core ADR 0198, plan S84 Decision 5): eleven
operations, named after what a purchasing department does and not after a vendor, served by a replaceable
**backend**. A workflow names the connector `purchasing` and these operations; a new purchasing system is a
new backend of the same server and no workflow changes. The descriptor, [`servers/purchasing.yaml`](../servers/purchasing.yaml),
declares exactly these operations, and `tests/test_descriptor.py` fails when the two drift.

## What this version serves

**Only the seeded fake serves the profile.** `PURCHASING_FAKE=1` (the descriptor's `fake.env`, never a
configuration value) selects it; `PURCHASING_FAKE_SCENE` picks its data. The configuration's `backend` is an
enum of the backends the server will serve, today `[odoo]`, and **selecting it makes the server refuse to
start**, naming the later version of the plugin that brings it (PA-06). Nothing here claims the profile works
against a real system: PA-06 maps each operation to Odoo's models, or says it is not supported.

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
  replay inside a governed action, not one after a restart (a real backend should also make its write
  idempotent by a reference of its own).
- **Every write is `human_approval: true`**, a floor an organisation may raise and never lower.
- **Typed filters, never a query string.** Filters are fields; a backend turns them into its own query form by
  code. Listings are paged by `limit` (1 to 100, 100 by default) and `cursor` (opaque: the previous
  page's `next_cursor` handed back as it came, absent for the first page; any other value is `refused`), and
  answered as `{<rows>, total, next_cursor}` where `next_cursor` is `null` on the last page and `<rows>` is
  `suppliers`, `agreements` or `contracts`. The core's job commands (S84-12) read exactly these shapes.
- **Dates** are `YYYY-MM-DD`; **amounts** are text with a decimal point and are never floats. The server
  computes a purchase request's line totals and total, exactly (two decimals); a caller's arithmetic is not
  trusted.
- **Errors** reach the agent as `<code>: <message>`: `refused` (the call breaks a rule; nothing reached the
  backend), `not_found`, `conflict` (the records' state forbids it), `backend_error`.
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
`end_date`, `currency`, `value`, `notes`. An **agreement**: `id`, `supplier_id`, `title`, `category`,
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

`backend` (`odoo`, not yet served), `base_url` (https), `database`, `username`, and the secret `api_key`
(`PURCHASING_*`). The only host the server may reach is the one in `base_url`. This version reads none of them:
it starts on the fake or not at all.
