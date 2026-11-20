# SPDX-License-Identifier: Apache-2.0
"""The eleven operations of the profile, independent of MCP and of the backend.

Each method validates its arguments (typed filters, bounded pages, dates, decimal amounts written as text),
calls the backend through `backends.base.Backend`, and returns a plain dict. The rules that hold whatever the
backend is live here:

- **Idempotence.** Every write takes `action_key`, the platform's key for one governed action (it never
  comes from the agent). A write without one is refused. The same key with the same call returns the first
  result and writes nothing; the same key with another call is a conflict. The memory lives in the process
  (it is the platform that replays a call within an action, not days later); a failed write is not
  remembered, so a retry runs it again.
- **No bank detail.** Nothing here takes or returns one; `refuse_bank_details` (app.py, on every call) and the
  closed list of supplier fields keep it that way.
- **Duplicates.** A supplier is not created when an active one has the same registration number in the
  same country, or, with none given, the same name.
- **Arithmetic is code.** A purchase request's line totals and total are computed here, exactly, never by the
  caller.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .backends.base import Backend
from .errors import Conflict, Refused
from .profile import (
    DEFAULT_PAGE,
    MAX_LINES,
    MAX_NAME,
    MAX_PAGE,
    MAX_TEXT,
    OPTIONAL_SUPPLIER_FIELDS,
    SUPPLIER_FIELDS,
    NewPurchaseRequest,
    NewSupplier,
    Page,
    RequestLine,
    decode_cursor,
    money,
    normalise_name,
    normalise_registration,
    parse_amount,
    parse_date,
    refuse_bank_details,
)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_COUNTRY = re.compile(r"^[A-Za-z]{2}$")
_CURRENCY = re.compile(r"^[A-Za-z]{3}$")
_CATEGORY = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_KEY = re.compile(r"^[\x21-\x7e]{1,200}$")


def _text(value: object, name: str, *, limit: int = MAX_TEXT, required: bool = True) -> str:
    if not isinstance(value, str):
        raise Refused(f"{name} must be text")
    text = value.strip()
    if required and not text:
        raise Refused(f"{name} is required")
    if len(text) > limit:
        raise Refused(f"{name} is longer than {limit} characters")
    return text


def _identifier(value: object, name: str) -> str:
    text = _text(value, name, limit=64)
    if not _ID.match(text):
        raise Refused(f"{name} is not an identifier of the purchasing system: {text!r}")
    return text


def _country(value: object) -> str:
    text = _text(value, "country", limit=2)
    if not _COUNTRY.match(text):
        raise Refused("country must be an ISO 3166-1 alpha-2 code, such as FR")
    return text.upper()


def _currency(value: object) -> str:
    text = _text(value, "currency", limit=3)
    if not _CURRENCY.match(text):
        raise Refused("currency must be an ISO 4217 code, such as EUR")
    return text.upper()


def _categories(value: object) -> list[str]:
    if not isinstance(value, list):
        raise Refused("categories must be a list of text")
    cleaned = [_text(item, "a category", limit=63).casefold() for item in value]
    for category in cleaned:
        if not _CATEGORY.match(category):
            raise Refused(f"category {category!r} must be lower-case words joined by hyphens, such as office-supplies")
    return sorted(set(cleaned))


def _paging(limit: int, cursor: str | None) -> tuple[int, int]:
    if not 1 <= limit <= MAX_PAGE:
        raise Refused(f"limit must be between 1 and {MAX_PAGE}")
    return limit, decode_cursor(cursor) if cursor else 0


def _page[T](page: Page[T], key: str, dump: Callable[[T], dict[str, Any]]) -> dict[str, Any]:
    return {key: [dump(item) for item in page.items], "total": page.total, "next_cursor": page.next_cursor}


def _supplier_field(name: str, value: object) -> object:
    """One supplier field, validated and normalised; `None` (or an empty text) clears an optional one."""
    if name == "categories":
        return _categories(value)
    if value is None or (isinstance(value, str) and not value.strip()):
        if name in OPTIONAL_SUPPLIER_FIELDS:
            return None
        raise Refused(f"{name} cannot be cleared")
    if name == "country":
        return _country(value)
    if name == "email":
        email = _text(value, name, limit=254)
        if not _EMAIL.match(email):
            raise Refused("email is not an address")
        return email
    return _text(value, name, limit=MAX_NAME if name == "name" else 120)


@dataclass
class _Done:
    operation: str
    fingerprint: str
    result: dict[str, Any]


class PurchasingTools:
    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self._done: dict[str, _Done] = {}
        self._lock = asyncio.Lock()

    # ─────────────────────────── the idempotent write ───────────────────────────

    async def _write(
        self,
        operation: str,
        action_key: str | None,
        arguments: dict[str, Any],
        perform: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        if action_key is None or not _KEY.match(action_key):
            raise Refused(
                f"{operation} needs an action_key, the platform's idempotency key (1 to 200 visible characters)"
            )
        fingerprint = json.dumps(arguments, sort_keys=True, default=str)
        async with self._lock:
            seen = self._done.get(action_key)
            if seen is not None:
                if (seen.operation, seen.fingerprint) != (operation, fingerprint):
                    raise Conflict(f"action_key {action_key!r} was already used for another call; nothing was written")
                return copy.deepcopy(seen.result)
            result = await perform()
            self._done[action_key] = _Done(operation, fingerprint, copy.deepcopy(result))
            return result

    # ─────────────────────────── reads ───────────────────────────

    async def search_suppliers(
        self,
        name: str | None = None,
        registration_number: str | None = None,
        country: str | None = None,
        include_archived: bool = False,
        limit: int = DEFAULT_PAGE,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        limit, offset = _paging(limit, cursor)
        page = await self.backend.search_suppliers(
            name=_text(name, "name", limit=MAX_NAME) if name is not None else None,
            registration_number=_text(registration_number, "registration_number", limit=64)
            if registration_number is not None
            else None,
            country=_country(country) if country is not None else None,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
        return _page(page, "suppliers", lambda s: s.model_dump(mode="json"))

    async def get_supplier(self, supplier_id: str) -> dict[str, Any]:
        supplier = await self.backend.get_supplier(_identifier(supplier_id, "supplier_id"))
        return {"supplier": supplier.model_dump(mode="json")}

    async def list_framework_agreements(
        self,
        category: str | None = None,
        valid_on: str | None = None,
        limit: int = DEFAULT_PAGE,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        limit, offset = _paging(limit, cursor)
        wanted = None
        if category is not None:
            wanted = _text(category, "category", limit=63).casefold()
        page = await self.backend.list_framework_agreements(
            category=wanted,
            valid_on=parse_date(valid_on, "valid_on") if valid_on is not None else None,
            limit=limit,
            offset=offset,
        )
        return _page(page, "agreements", lambda a: a.model_dump(mode="json"))

    async def list_contracts(
        self,
        ending_before: str | None = None,
        supplier_id: str | None = None,
        limit: int = DEFAULT_PAGE,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        limit, offset = _paging(limit, cursor)
        page = await self.backend.list_contracts(
            ending_before=parse_date(ending_before, "ending_before") if ending_before is not None else None,
            supplier_id=_identifier(supplier_id, "supplier_id") if supplier_id is not None else None,
            limit=limit,
            offset=offset,
        )
        return _page(
            page, "contracts", lambda c: {**c.model_dump(mode="json", exclude={"notes"}), "note_count": len(c.notes)}
        )

    async def get_contract(self, contract_id: str) -> dict[str, Any]:
        contract = await self.backend.get_contract(_identifier(contract_id, "contract_id"))
        return {"contract": contract.model_dump(mode="json")}

    # ─────────────────────────── writes ───────────────────────────

    async def _duplicates(
        self, name: str, country: str, registration: str | None, *, ignore: str | None = None
    ) -> list[str]:
        """Ids of the active suppliers a record with these identifiers would duplicate."""
        found: list[str] = []
        offset = 0
        while True:  # every page: a duplicate is not missed because it is the 101st
            page = await self.backend.search_suppliers(
                name=None,
                registration_number=registration,
                country=country,
                include_archived=False,
                limit=MAX_PAGE,
                offset=offset,
            )
            for s in page.items:
                if s.id != ignore and (registration or normalise_name(s.name) == normalise_name(name)):
                    found.append(s.id)
            offset += len(page.items)
            if not page.items or offset >= page.total:
                return found

    async def create_supplier(
        self,
        name: str,
        country: str,
        registration_number: str | None = None,
        vat_number: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        website: str | None = None,
        address_line: str | None = None,
        city: str | None = None,
        postal_code: str | None = None,
        categories: list[str] | None = None,
        action_key: str | None = None,
    ) -> dict[str, Any]:
        given: dict[str, object] = {
            "name": name,
            "country": country,
            "registration_number": registration_number,
            "vat_number": vat_number,
            "email": email,
            "phone": phone,
            "website": website,
            "address_line": address_line,
            "city": city,
            "postal_code": postal_code,
            "categories": categories or [],
        }
        clean = {key: _supplier_field(key, value) for key, value in given.items()}
        new = NewSupplier.model_validate(clean)

        async def perform() -> dict[str, Any]:
            registration = normalise_registration(new.registration_number) if new.registration_number else None
            duplicates = await self._duplicates(new.name, new.country, registration)
            if duplicates:
                raise Conflict(
                    f"duplicate_supplier: an active supplier already has this identity ({', '.join(duplicates)}); "
                    "use it, or update it, instead of creating another"
                )
            supplier = await self.backend.create_supplier(new)
            return {"supplier_id": supplier.id, "supplier": supplier.model_dump(mode="json"), "created": True}

        return await self._write("create_supplier", action_key, clean, perform)

    async def update_supplier(
        self, supplier_id: str, fields: dict[str, Any] | str, action_key: str | None = None
    ) -> dict[str, Any]:
        sid = _identifier(supplier_id, "supplier_id")
        wanted = _fields(fields)
        clean = {key: _supplier_field(key, value) for key, value in wanted.items()}

        async def perform() -> dict[str, Any]:
            current = await self.backend.get_supplier(sid)
            if current.status == "archived":
                raise Conflict(f"supplier {sid} is archived; an archived supplier is not edited")
            before = current.model_dump(mode="json")
            changes = {key: value for key, value in clean.items() if before[key] != value}
            if "registration_number" in changes or "country" in changes:
                registration = changes.get("registration_number", current.registration_number)
                country = str(changes.get("country", current.country))
                if registration:
                    duplicates = await self._duplicates(
                        current.name, country, normalise_registration(str(registration)), ignore=sid
                    )
                    if duplicates:
                        raise Conflict(f"duplicate_supplier: the identity collides with {', '.join(duplicates)}")
            updated = current
            if changes:
                updated = await self.backend.update_supplier(sid, changes)
            return {
                "supplier_id": sid,
                "supplier": updated.model_dump(mode="json"),
                "changed": sorted(changes),
                "previous": {key: before[key] for key in changes},
            }

        return await self._write("update_supplier", action_key, {"supplier_id": sid, "fields": clean}, perform)

    async def archive_supplier(
        self, supplier_id: str, reason: str | None = None, action_key: str | None = None
    ) -> dict[str, Any]:
        sid = _identifier(supplier_id, "supplier_id")
        why = _text(reason, "reason", limit=500) if reason else None

        async def perform() -> dict[str, Any]:
            current = await self.backend.get_supplier(sid)
            archived = current.status == "active"
            supplier = await self.backend.archive_supplier(sid, why) if archived else current
            return {
                "supplier_id": sid,
                "status": supplier.status,
                "previous_status": current.status,
                "archived": archived,
            }

        return await self._write("archive_supplier", action_key, {"supplier_id": sid, "reason": why}, perform)

    async def add_contract_note(self, contract_id: str, text: str, action_key: str | None = None) -> dict[str, Any]:
        cid = _identifier(contract_id, "contract_id")
        body = _text(text, "text")

        async def perform() -> dict[str, Any]:
            note = await self.backend.add_contract_note(cid, body)
            return {"contract_id": cid, "note_id": note.id, "created_at": note.created_at}

        return await self._write("add_contract_note", action_key, {"contract_id": cid, "text": body}, perform)

    async def create_purchase_request(
        self,
        supplier_id: str,
        title: str,
        lines: list[dict[str, str]],
        currency: str = "EUR",
        needed_by: str | None = None,
        contract_id: str | None = None,
        justification: str = "",
        action_key: str | None = None,
    ) -> dict[str, Any]:
        sid = _identifier(supplier_id, "supplier_id")
        cid = _identifier(contract_id, "contract_id") if contract_id else None
        heading = _text(title, "title", limit=MAX_NAME)
        code = _currency(currency)
        due = parse_date(needed_by, "needed_by") if needed_by else None
        why = _text(justification, "justification", required=False)
        if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
            raise Refused(f"lines must hold 1 to {MAX_LINES} lines")
        built: list[RequestLine] = []
        total = Decimal(0)
        for index, line in enumerate(lines, start=1):
            if not isinstance(line, dict) or not {"description", "quantity", "unit_price"} <= set(line):
                raise Refused(f"line {index} needs description, quantity and unit_price")
            unknown = set(line) - {"description", "quantity", "unit", "unit_price"}
            if unknown:
                raise Refused(f"line {index}: unknown field {sorted(unknown)[0]!r}")
            quantity = parse_amount(line["quantity"], f"line {index} quantity", positive=True)
            price = parse_amount(line["unit_price"], f"line {index} unit_price", positive=False)
            line_total = quantity * price
            total += line_total
            built.append(
                RequestLine(
                    description=_text(line["description"], f"line {index} description", limit=MAX_NAME),
                    quantity=f"{quantity.normalize():f}",
                    unit=_text(line.get("unit", "unit"), f"line {index} unit", limit=32),
                    unit_price=money(price),
                    line_total=money(line_total),
                )
            )
        new = NewPurchaseRequest(
            supplier_id=sid,
            contract_id=cid,
            title=heading,
            lines=built,
            currency=code,
            total=money(total),
            needed_by=due,
            justification=why,
        )

        async def perform() -> dict[str, Any]:
            supplier = await self.backend.get_supplier(sid)
            if supplier.status != "active":
                raise Conflict(f"supplier {sid} is archived; a purchase request needs an active supplier")
            if cid is not None:
                contract = await self.backend.get_contract(cid)
                if contract.supplier_id != sid:
                    raise Conflict(f"contract {cid} belongs to {contract.supplier_id}, not to {sid}")
            request = await self.backend.create_purchase_request(new)
            return {
                "request_id": request.id,
                "status": request.status,
                "total": request.total,
                "currency": request.currency,
                "request": request.model_dump(mode="json"),
            }

        return await self._write("create_purchase_request", action_key, new.model_dump(mode="json"), perform)

    async def cancel_purchase_request(
        self, request_id: str, reason: str, action_key: str | None = None
    ) -> dict[str, Any]:
        rid = _identifier(request_id, "request_id")
        why = _text(reason, "reason", limit=500)

        async def perform() -> dict[str, Any]:
            current = await self.backend.get_purchase_request(rid)
            cancelled = current.status != "cancelled"
            request = await self.backend.cancel_purchase_request(rid, why) if cancelled else current
            return {
                "request_id": rid,
                "status": request.status,
                "previous_status": current.status,
                "cancelled": cancelled,
            }

        return await self._write("cancel_purchase_request", action_key, {"request_id": rid, "reason": why}, perform)


def _fields(fields: dict[str, Any] | str) -> dict[str, Any]:
    """`fields` as an object, or as its JSON text (the platform renders every argument to a string)."""
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except ValueError as error:
            raise Refused("fields must be an object, or the JSON text of one") from error
    if not isinstance(fields, dict):
        raise Refused("fields must be an object")
    refuse_bank_details(fields, "fields")
    unknown = sorted(set(fields) - set(SUPPLIER_FIELDS))
    if unknown:
        raise Refused(f"cannot change {', '.join(unknown)}; the fields are {', '.join(SUPPLIER_FIELDS)}")
    return fields
