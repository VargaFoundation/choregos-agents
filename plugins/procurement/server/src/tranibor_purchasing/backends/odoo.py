# SPDX-License-Identifier: Apache-2.0
"""The Odoo backend: the purchasing profile served from Odoo 19 and 20 (Purchase, Purchase Agreements, Contacts).

The API is Odoo's **external JSON-2 API** (`POST /json/2/<model>/<method>`, `Authorization: bearer <API key>`),
the one the vendor documents for 19.0 and 20.0; the older XML-RPC and JSON-RPC endpoints are deprecated there
(removal announced for Odoo 22) and Odoo 18 has no JSON-2, so Odoo 18 is not served. Every operation's mapping,
the pages read and what is *not supported* are written in `PROFILE.md`, "The Odoo backend"; this module is that
mapping as code.

What the code guarantees, whatever Odoo answers:

- **Typed filters.** A domain is built here from validated values (`_supplier_domain`, ...): a caller's text is
  always the *value* of one term, never part of a query. Model and method names are constants.
- **Nothing about a bank.** Every read names its fields; no list holds `bank_ids` or anything named like one, and
  no write sets one (`tests/test_odoo_backend.py` scans every request).
- **Bounded.** Timeouts on every call, a page is at most `MAX_PAGE` rows, an answer larger than `MAX_ANSWER`
  bytes is refused, redirects are not followed (the one host is the configured one), and an error shows Odoo's
  message and never its traceback, the request or the key.
- **Idempotent across restarts** for the three writes whose replay would create a second record: the record
  carries a reference derived from the platform's `action_key` (`tranibor-<hash>`), searched before writing.
  It does not cover two identical calls racing on two replicas, nor an `action_key` reused for another call after
  a restart (the first record is returned instead of a `conflict`).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import SecretStr

from .. import __version__
from ..config import Settings
from ..errors import BackendError, Conflict, NotFound, NotSupported, Refused
from ..profile import (
    MAX_PAGE,
    Agreement,
    Contract,
    NewPurchaseRequest,
    NewSupplier,
    Note,
    Page,
    PurchaseRequest,
    RequestLine,
    Supplier,
    money,
    normalise_registration,
)

MAX_ANSWER = 8 * 1024 * 1024
#: Registration numbers are compared after normalisation, which Odoo cannot do: the candidates a loose pattern
#: finds are filtered here, and there are at most this many.
MAX_CANDIDATES = 500
MAX_NOTES = 1000

_SUPPLIER = ["supplier_rank", ">", 0]
_NOT_A_CONTACT = ["parent_id", "=", False]
_ANY_ACTIVE = ["active", "in", [True, False]]
_PARTNER_FIELDS = [
    "name",
    "vat",
    "country_code",
    "email",
    "phone",
    "website",
    "street",
    "city",
    "zip",
    "category_id",
    "active",
    "create_date",
    "write_date",
]
_AGREEMENT_FIELDS = ["name", "reference", "vendor_id", "date_start", "date_end", "currency_id", "description"]
_ORDER_FIELDS = ["partner_id", "requisition_id", "note", "currency_id", "state", "date_planned", "create_date"]
_LINE_FIELDS = ["name", "product_qty", "price_unit"]
#: Odoo's order states in which a purchase request is still a request: nothing is ordered.
_DRAFT_STATES = ("draft", "sent")
_UNIT = re.compile(r"\s\[unit: ([^\]]{1,32})\]$")


def _marker(action_key: str) -> str:
    """The reference stored in a record the `action_key` created: the key's hash, never the key."""
    return "tranibor-" + hashlib.sha256(action_key.encode()).hexdigest()[:32]


def _message_id(action_key: str) -> str:
    return f"<{_marker(action_key)}@purchasing.invalid>"


def _odoo_id(value: str, what: str) -> int:
    if not value.isdigit() or len(value) > 12 or int(value) < 1:
        raise NotFound(f"no {what} {value}")
    return int(value)


def _stamp(value: object) -> str:
    """Odoo's UTC datetime `YYYY-MM-DD HH:MM:SS` as the profile's ISO timestamp."""
    return str(value).replace(" ", "T") + "Z" if value else ""


def _date(value: object) -> date | None:
    return date.fromisoformat(str(value)[:10]) if value else None


def _text(value: object) -> str:
    """The text of an HTML field written by people: tags removed, entities decoded, never rendered."""
    if not value:
        return ""
    plain = re.sub(r"<\s*(br\s*/?|/p|/div|/li)\s*>", "\n", str(value), flags=re.IGNORECASE)
    return html.unescape(re.sub(r"<[^>]*>", "", plain)).strip()


def _optional(value: object) -> str | None:
    return str(value) if value else None


def _many2one(value: object) -> tuple[int, str] | None:
    if isinstance(value, list | tuple) and len(value) == 2 and isinstance(value[0], int):
        return value[0], str(value[1])
    return None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


def _decimals(value: str) -> int:
    exponent = Decimal(value).as_tuple().exponent
    return -exponent if isinstance(exponent, int) else 0


def _amount(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise BackendError(f"odoo answered a number this server cannot read: {value!r}") from error


class OdooJson2:
    """The transport: one JSON-2 call, errors turned into this server's."""

    def __init__(
        self,
        base_url: str,
        api_key: SecretStr,
        database: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {
            "Authorization": f"bearer {api_key.get_secret_value()}",
            "User-Agent": f"tranibor-purchasing/{__version__}",
        }
        if database:
            headers["X-Odoo-Database"] = database
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(20.0, connect=5.0, pool=5.0),
            follow_redirects=False,
            transport=transport,
        )

    async def call(self, model: str, method: str, *, ids: Sequence[int] | None = None, **named: Any) -> Any:
        payload: dict[str, Any] = dict(named)
        if ids is not None:
            payload["ids"] = list(ids)
        try:
            async with self._client.stream("POST", f"/json/2/{model}/{method}", json=payload) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_ANSWER:
                        raise BackendError(f"odoo's answer to {model}.{method} is larger than {MAX_ANSWER} bytes")
                status = response.status_code
        except httpx.TimeoutException as error:
            raise BackendError(f"odoo did not answer {model}.{method} in time") from error
        except httpx.HTTPError as error:
            raise BackendError(f"odoo could not be reached ({type(error).__name__})") from error
        if 300 <= status < 400:
            raise BackendError(
                "odoo redirected the call; redirects are not followed (the only host is the configured one)"
            )
        decoded = _json(bytes(body))
        if status == 200:
            return decoded
        message, name = "", ""
        if isinstance(decoded, dict):
            message = str(decoded.get("message", ""))[:300]
            name = str(decoded.get("name", ""))
        if status in (401, 403):
            raise BackendError(
                f"odoo refused the call ({status}): the API key is invalid, expired, or the integration user lacks the "
                f"right for {model}.{method}. {message}".strip()
            )
        if status == 404 and name.endswith("MissingError"):
            raise NotFound(f"odoo: {message or 'the record does not exist'}")
        raise BackendError(f"odoo answered {status} to {model}.{method}: {message or 'no message'}")


def _json(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError as error:
        raise BackendError("odoo answered something that is not JSON") from error


class OdooPurchasing:
    """The profile on Odoo. One instance per process."""

    name = "odoo"

    def __init__(self, rpc: OdooJson2) -> None:
        self.rpc = rpc
        self._countries: dict[str, int] = {}
        self._has_registry: bool | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> OdooPurchasing:
        if settings.base_url is None or settings.api_key is None:  # Settings checked; this narrows the types
            raise ValueError("the odoo backend needs PURCHASING_BASE_URL and PURCHASING_API_KEY")
        return cls(OdooJson2(settings.base_url, settings.api_key, settings.database))

    # ─────────────────────────── helpers ───────────────────────────

    async def _search_read(
        self, model: str, domain: list[Any], fields: list[str], *, order: str, limit: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        rows = await self.rpc.call(
            model, "search_read", domain=domain, fields=fields, order=order, limit=limit, offset=offset
        )
        if not isinstance(rows, list):
            raise BackendError(f"odoo answered {model}.search_read with something that is not a list")
        return [row for row in rows if isinstance(row, dict)]

    async def _count(self, model: str, domain: list[Any]) -> int:
        count = await self.rpc.call(model, "search_count", domain=domain)
        if not isinstance(count, int):
            raise BackendError(f"odoo answered {model}.search_count with something that is not a number")
        return count

    async def _registry_supported(self) -> bool:
        """Odoo 19 keeps a supplier's registration number in `res.partner.company_registry`; Odoo 20 replaced it
        with per-country `additional_identifiers`, which this mapping does not read. Asked once, of Odoo."""
        if self._has_registry is None:
            fields = await self.rpc.call(
                "res.partner", "fields_get", allfields=["company_registry"], attributes=["type"]
            )
            self._has_registry = isinstance(fields, dict) and "company_registry" in fields
        return self._has_registry

    async def _need_registry(self) -> None:
        if not await self._registry_supported():
            raise NotSupported(
                "registration_number is not supported by this backend on this Odoo: it has no `company_registry` "
                "field on a partner (Odoo 20 keeps such numbers per country in `additional_identifiers`)"
            )

    async def _country_id(self, code: str) -> int:
        if code not in self._countries:
            rows = await self._search_read("res.country", [["code", "=", code]], ["code"], order="id asc", limit=1)
            if not rows:
                raise Refused(f"country {code} is not a country of this Odoo")
            self._countries[code] = int(rows[0]["id"])
        return self._countries[code]

    async def _tag_ids(self, categories: list[str]) -> list[int]:
        """Odoo contact tags named like the categories (`office-supplies` finds `Office Supplies` too); a missing
        one is created."""
        if not categories:
            return []
        names = sorted({n for c in categories for n in (c, c.replace("-", " "))})
        domain: list[Any] = ["|"] * (len(names) - 1) + [["name", "=ilike", n] for n in names]
        rows = await self._search_read("res.partner.category", domain, ["name"], order="id asc", limit=MAX_PAGE * 5)
        found: dict[str, int] = {}
        for row in rows:
            found.setdefault(_slug(str(row["name"])), int(row["id"]))
        ids: list[int] = []
        for category in categories:
            if category not in found:
                created = await self.rpc.call("res.partner.category", "create", vals_list=[{"name": category}])
                found[category] = int(created[0])
            ids.append(found[category])
        return ids

    async def _suppliers(self, rows: list[dict[str, Any]]) -> list[Supplier]:
        tag_ids = sorted({t for row in rows for t in (row.get("category_id") or [])})
        names: dict[int, str] = {}
        if tag_ids:
            tags = await self.rpc.call("res.partner.category", "read", ids=tag_ids, fields=["name"])
            names = {int(t["id"]): str(t["name"]) for t in tags}
        registry = await self._registry_supported() if rows else False
        suppliers: list[Supplier] = []
        for row in rows:
            suppliers.append(
                Supplier(
                    id=str(row["id"]),
                    name=str(row["name"]),
                    registration_number=_optional(row.get("company_registry")) if registry else None,
                    vat_number=_optional(row.get("vat")),
                    country=str(row.get("country_code") or ""),
                    email=_optional(row.get("email")),
                    phone=_optional(row.get("phone")),
                    website=_optional(row.get("website")),
                    address_line=_optional(row.get("street")),
                    city=_optional(row.get("city")),
                    postal_code=_optional(row.get("zip")),
                    categories=sorted({s for t in row.get("category_id") or [] if (s := _slug(names.get(t, "")))}),
                    status="active" if row.get("active", True) else "archived",
                    archived_reason=None,  # kept in the chatter, not in a field
                    created_at=_stamp(row.get("create_date")),
                    updated_at=_stamp(row.get("write_date")),
                )
            )
        return suppliers

    async def _partner_fields(self) -> list[str]:
        return [*_PARTNER_FIELDS, "company_registry"] if await self._registry_supported() else list(_PARTNER_FIELDS)

    # ─────────────────────────── suppliers ───────────────────────────

    async def search_suppliers(
        self,
        *,
        name: str | None,
        registration_number: str | None,
        country: str | None,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> Page[Supplier]:
        domain: list[Any] = [_SUPPLIER, _NOT_A_CONTACT]
        if include_archived:
            domain.append(_ANY_ACTIVE)
        if name is not None:
            domain.append(["name", "ilike", name])  # Odoo escapes % and _ in an `ilike` value
        if country is not None:
            domain.append(["country_id.code", "=", country])
        fields = await self._partner_fields()
        if registration_number is None:
            total = await self._count("res.partner", domain)
            rows = await self._search_read(
                "res.partner", domain, fields, order="name asc, id asc", limit=limit, offset=offset
            )
            return Page[Supplier](items=await self._suppliers(rows), total=total, limit=limit, offset=offset)
        await self._need_registry()
        wanted = normalise_registration(registration_number)
        letters = re.sub(r"[^A-Za-z0-9]", "", wanted)
        if not letters:
            return Page[Supplier](items=[], total=0, limit=limit, offset=offset)
        # Odoo cannot compare a number ignoring spaces and dots: a loose pattern finds the candidates (every
        # character, in order), and the exact comparison is made here.
        domain.append(["company_registry", "=ilike", "%" + "%".join(letters) + "%"])
        rows = await self._search_read(
            "res.partner", domain, fields, order="name asc, id asc", limit=MAX_CANDIDATES + 1
        )
        if len(rows) > MAX_CANDIDATES:
            raise BackendError(
                f"more than {MAX_CANDIDATES} partners look like this registration number; narrow the search"
            )
        exact = [r for r in rows if normalise_registration(str(r.get("company_registry") or "")) == wanted]
        exact.sort(key=lambda r: (str(r["name"]).casefold(), int(r["id"])))
        page = exact[offset : offset + limit]
        return Page[Supplier](items=await self._suppliers(page), total=len(exact), limit=limit, offset=offset)

    async def get_supplier(self, supplier_id: str) -> Supplier:
        number = _odoo_id(supplier_id, "supplier")
        domain = [["id", "=", number], _SUPPLIER, _NOT_A_CONTACT, _ANY_ACTIVE]
        rows = await self._search_read("res.partner", domain, await self._partner_fields(), order="id asc", limit=1)
        if not rows:
            raise NotFound(f"no supplier {supplier_id}")
        return (await self._suppliers(rows))[0]

    async def _partner_values(self, values: dict[str, Any]) -> dict[str, Any]:
        """Profile supplier fields as Odoo `res.partner` values; `None` clears (`False` in Odoo)."""
        if "registration_number" in values:
            await self._need_registry()  # before anything else is asked of Odoo
        out: dict[str, Any] = {}
        for key, value in values.items():
            if key == "registration_number":
                out["company_registry"] = value or False
            elif key == "country":
                out["country_id"] = await self._country_id(value)
            elif key == "categories":
                out["category_id"] = [[6, 0, await self._tag_ids(value)]]
            else:
                column = {"vat_number": "vat", "address_line": "street", "postal_code": "zip"}.get(key, key)
                out[column] = value if value is not None else False
        return out

    async def create_supplier(self, new: NewSupplier, *, action_key: str | None = None) -> Supplier:
        values = await self._partner_values(new.model_dump(exclude_none=True))
        values["supplier_rank"] = 1
        if action_key:
            values["comment"] = f"<p>Created through the purchasing integration ({_marker(action_key)}).</p>"
        created = await self.rpc.call("res.partner", "create", vals_list=[values])
        return await self.get_supplier(str(int(created[0])))

    async def supplier_created_by(self, action_key: str) -> Supplier | None:
        domain = [["comment", "ilike", _marker(action_key)], _SUPPLIER, _NOT_A_CONTACT, _ANY_ACTIVE]
        rows = await self._search_read("res.partner", domain, await self._partner_fields(), order="id asc", limit=1)
        return (await self._suppliers(rows))[0] if rows else None

    async def update_supplier(self, supplier_id: str, changes: dict[str, Any]) -> Supplier:
        number = _odoo_id(supplier_id, "supplier")
        await self.get_supplier(supplier_id)
        await self.rpc.call("res.partner", "write", ids=[number], vals=await self._partner_values(changes))
        return await self.get_supplier(supplier_id)

    async def archive_supplier(self, supplier_id: str, reason: str | None) -> Supplier:
        number = _odoo_id(supplier_id, "supplier")
        await self.get_supplier(supplier_id)
        await self.rpc.call("res.partner", "write", ids=[number], vals={"active": False})
        if reason:
            await self._post(
                "res.partner", number, f"Archived through the purchasing integration. Reason: {reason}", None
            )
        return await self.get_supplier(supplier_id)

    async def _post(self, model: str, number: int, body: str, message_id: str | None) -> int:
        """An internal note in the record's chatter (`body` is plain text: Odoo escapes it)."""
        extra: dict[str, Any] = {"message_id": message_id} if message_id else {}
        posted = await self.rpc.call(
            model,
            "message_post",
            ids=[number],
            body=body,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
            **extra,
        )
        value = posted[0] if isinstance(posted, list) and posted else posted
        if not isinstance(value, int):
            raise BackendError("odoo did not say which note it posted")
        return value

    # ─────────────────────────── agreements and contracts ───────────────────────────

    @staticmethod
    def _requisitions(extra: list[Any]) -> list[Any]:
        """Blanket orders that were confirmed (or closed since), with a vendor and a start date: the profile
        requires both. Drafts and cancelled ones were never in force."""
        return [
            ["requisition_type", "=", "blanket_order"],
            ["state", "in", ["confirmed", "done"]],
            ["vendor_id", "!=", False],
            ["date_start", "!=", False],
            *extra,
        ]

    @staticmethod
    def _agreement(row: dict[str, Any]) -> Agreement:
        vendor, currency = _many2one(row.get("vendor_id")), _many2one(row.get("currency_id"))
        start = _date(row.get("date_start"))
        if vendor is None or start is None or currency is None:
            raise BackendError(f"odoo's agreement {row.get('id')} lacks a vendor, a start date or a currency")
        return Agreement(
            id=str(row["id"]),
            supplier_id=str(vendor[0]),
            title=str(row.get("reference") or row["name"]),
            category="",  # an Odoo agreement has no category
            valid_from=start,
            valid_to=_date(row.get("date_end")),
            currency=currency[1],
            terms_summary=_text(row.get("description"))[:2000],
        )

    async def list_framework_agreements(
        self, *, category: str | None, valid_on: date | None, limit: int, offset: int
    ) -> Page[Agreement]:
        if category is not None:
            raise NotSupported(
                "the category filter is not supported by this backend: an Odoo agreement has no category"
            )
        extra: list[Any] = []
        if valid_on is not None:
            day = valid_on.isoformat()
            extra = [
                ["state", "=", "confirmed"],
                ["date_start", "<=", day],
                "|",
                ["date_end", "=", False],
                ["date_end", ">=", day],
            ]
        domain = self._requisitions(extra)
        total = await self._count("purchase.requisition", domain)
        rows = await self._search_read(
            "purchase.requisition", domain, _AGREEMENT_FIELDS, order="id asc", limit=limit, offset=offset
        )
        return Page[Agreement](items=[self._agreement(r) for r in rows], total=total, limit=limit, offset=offset)

    async def _notes(self, numbers: list[int], *, with_text: bool) -> dict[int, list[Note]]:
        """The comments of the agreements (their chatter), oldest first. Without `with_text` the notes only count."""
        if not numbers:
            return {}
        domain = [["model", "=", "purchase.requisition"], ["res_id", "in", numbers], ["message_type", "=", "comment"]]
        fields = ["res_id", "body", "date"] if with_text else ["res_id", "date"]
        rows = await self._search_read("mail.message", domain, fields, order="date asc, id asc", limit=MAX_NOTES)
        notes: dict[int, list[Note]] = {n: [] for n in numbers}
        for row in rows:
            notes[int(row["res_id"])].append(
                Note(id=str(row["id"]), text=_text(row.get("body")), created_at=_stamp(row.get("date")))
            )
        return notes

    async def _contracts(self, rows: list[dict[str, Any]], *, with_text: bool) -> list[Contract]:
        notes = await self._notes([int(r["id"]) for r in rows], with_text=with_text)
        contracts: list[Contract] = []
        for row in rows:
            agreement = self._agreement(row)
            contracts.append(
                Contract(
                    id=agreement.id,
                    supplier_id=agreement.supplier_id,
                    title=agreement.title,
                    category="",
                    start_date=agreement.valid_from,
                    end_date=agreement.valid_to,
                    currency=agreement.currency,
                    value=None,  # an Odoo agreement carries no total
                    notes=notes[int(row["id"])],
                )
            )
        return contracts

    async def list_contracts(
        self, *, ending_before: date | None, supplier_id: str | None, limit: int, offset: int
    ) -> Page[Contract]:
        extra: list[Any] = []
        if supplier_id is not None:
            if not supplier_id.isdigit() or int(supplier_id) < 1:
                return Page[Contract](items=[], total=0, limit=limit, offset=offset)
            extra.append(["vendor_id", "=", int(supplier_id)])
        order = "id asc"
        if ending_before is not None:
            extra += [["date_end", "!=", False], ["date_end", "<", ending_before.isoformat()]]
            order = "date_end asc, id asc"
        domain = self._requisitions(extra)
        total = await self._count("purchase.requisition", domain)
        rows = await self._search_read(
            "purchase.requisition", domain, _AGREEMENT_FIELDS, order=order, limit=limit, offset=offset
        )
        return Page[Contract](
            items=await self._contracts(rows, with_text=False), total=total, limit=limit, offset=offset
        )

    async def _requisition(self, contract_id: str) -> dict[str, Any]:
        number = _odoo_id(contract_id, "contract")
        rows = await self._search_read(
            "purchase.requisition",
            self._requisitions([["id", "=", number]]),
            _AGREEMENT_FIELDS,
            order="id asc",
            limit=1,
        )
        if not rows:
            raise NotFound(f"no contract {contract_id}")
        return rows[0]

    async def get_contract(self, contract_id: str) -> Contract:
        return (await self._contracts([await self._requisition(contract_id)], with_text=True))[0]

    async def add_contract_note(self, contract_id: str, text: str, *, action_key: str | None = None) -> Note:
        number = int((await self._requisition(contract_id))["id"])
        message_id = _message_id(action_key) if action_key else None
        if message_id:
            known = await self._search_read(
                "mail.message", [["message_id", "=", message_id]], ["body", "date"], order="id asc", limit=1
            )
            if known:
                return Note(
                    id=str(known[0]["id"]), text=_text(known[0].get("body")), created_at=_stamp(known[0].get("date"))
                )
        posted = await self._post("purchase.requisition", number, text, message_id)
        rows = await self._search_read("mail.message", [["id", "=", posted]], ["body", "date"], order="id asc", limit=1)
        if not rows:
            raise BackendError("odoo posted the note but does not show it")
        return Note(id=str(posted), text=_text(rows[0].get("body")), created_at=_stamp(rows[0].get("date")))

    # ─────────────────────────── purchase requests ───────────────────────────

    async def _currency_id(self, code: str) -> int:
        rows = await self._search_read("res.currency", [["name", "=", code]], ["name"], order="id asc", limit=1)
        if not rows:
            raise Refused(f"currency {code} is not an active currency of this Odoo")
        return int(rows[0]["id"])

    async def _request(self, domain: list[Any]) -> PurchaseRequest | None:
        rows = await self._search_read("purchase.order", domain, _ORDER_FIELDS, order="id asc", limit=1)
        if not rows:
            return None
        row = rows[0]
        state = str(row.get("state"))
        if state in _DRAFT_STATES:
            status = "draft"
        elif state == "cancel":
            status = "cancelled"
        else:
            raise Conflict(f"purchase order {row['id']} is {state!r}: it is no longer a draft request")
        partner, currency, agreement = (_many2one(row.get(k)) for k in ("partner_id", "currency_id", "requisition_id"))
        if partner is None or currency is None:
            raise BackendError(f"odoo's purchase order {row['id']} lacks a vendor or a currency")
        lines = await self._search_read(
            "purchase.order.line",
            [["order_id", "=", row["id"]], ["display_type", "=", False]],
            _LINE_FIELDS,
            order="sequence asc, id asc",
            limit=MAX_PAGE,
        )
        built: list[RequestLine] = []
        total = Decimal(0)
        for line in lines:
            quantity, price = _amount(line["product_qty"]), _amount(line["price_unit"])
            total += quantity * price
            description, unit = str(line["name"]), "unit"
            if found := _UNIT.search(description):
                description, unit = description[: found.start()], found.group(1)
            built.append(
                RequestLine(
                    description=description,
                    quantity=f"{quantity.normalize():f}",
                    unit=unit,
                    unit_price=money(price),
                    line_total=money(quantity * price),
                )
            )
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", str(row.get("note") or ""), flags=re.DOTALL | re.IGNORECASE)
        return PurchaseRequest(
            id=str(row["id"]),
            supplier_id=str(partner[0]),
            contract_id=str(agreement[0]) if agreement else None,
            title=_text(paragraphs[0]) if paragraphs else "",
            lines=built,
            currency=currency[1],
            total=money(total),
            needed_by=_date(row.get("date_planned")),
            justification=_text(paragraphs[1]) if len(paragraphs) > 1 else "",
            status=status,
            created_at=_stamp(row.get("create_date")),
        )

    async def get_purchase_request(self, request_id: str) -> PurchaseRequest:
        found = await self._request([["id", "=", _odoo_id(request_id, "purchase request")]])
        if found is None:
            raise NotFound(f"no purchase request {request_id}")
        return found

    async def create_purchase_request(
        self, new: NewPurchaseRequest, *, action_key: str | None = None
    ) -> PurchaseRequest:
        marker = _marker(action_key) if action_key else None
        if marker and (known := await self._request([["origin", "=", marker]])) is not None:
            return known
        planned = f"{new.needed_by.isoformat()} 12:00:00" if new.needed_by else None
        order_lines: list[Any] = []
        for line in new.lines:
            if _decimals(line.quantity) > 2:
                raise Refused(f"quantity {line.quantity} has more than two decimals, which this backend does not keep")
            name = line.description if line.unit == "unit" else f"{line.description} [unit: {line.unit}]"
            values: dict[str, Any] = {
                "name": name,
                "product_qty": float(Decimal(line.quantity)),
                "price_unit": float(Decimal(line.unit_price)),
            }
            if planned:
                values["date_planned"] = planned
            order_lines.append([0, 0, values])
        note = f"<p>{html.escape(new.title)}</p>" + (
            f"<p>{html.escape(new.justification)}</p>" if new.justification else ""
        )
        values = {
            "partner_id": _odoo_id(new.supplier_id, "supplier"),
            "currency_id": await self._currency_id(new.currency),
            "note": note,
            "order_line": order_lines,
        }
        if marker:
            values["origin"] = marker
        if new.contract_id:
            values["requisition_id"] = _odoo_id(new.contract_id, "contract")
        created = await self.rpc.call("purchase.order", "create", vals_list=[values])
        request = await self.get_purchase_request(str(int(created[0])))
        if request.total != new.total or [(x.quantity, x.unit_price) for x in request.lines] != [
            (x.quantity, x.unit_price) for x in new.lines
        ]:  # a database with fewer decimals than the profile's two would store other amounts: do not keep them
            await self.rpc.call("purchase.order", "button_cancel", ids=[int(request.id)])
            raise BackendError(
                f"odoo stored other amounts than asked (its decimal precision is lower than two); "
                f"the draft purchase order {request.id} was cancelled"
            )
        return request

    async def cancel_purchase_request(self, request_id: str, reason: str) -> PurchaseRequest:
        current = await self.get_purchase_request(request_id)
        if current.status == "cancelled":
            raise Conflict(f"purchase request {request_id} is already cancelled")
        number = int(request_id)
        await self.rpc.call("purchase.order", "button_cancel", ids=[number])
        await self._post(
            "purchase.order", number, f"Cancelled through the purchasing integration. Reason: {reason}", None
        )
        return await self.get_purchase_request(request_id)
