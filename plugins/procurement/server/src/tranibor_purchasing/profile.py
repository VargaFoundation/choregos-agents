# SPDX-License-Identifier: Apache-2.0
"""The profile of operations (PROFILE.md): the typed records every backend returns, and the rules that
hold whatever the backend is.

Nothing here knows a purchasing system. A backend maps its own records to these models; the tools
(`tools.py`) validate a call against these rules before the backend sees it.

No model has a field for a bank detail, and the guard below refuses one wherever it appears in a call.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import Refused

MAX_PAGE = 100
DEFAULT_PAGE = 100
MAX_TEXT = 2000
MAX_NAME = 200
MAX_LINES = 100

#: Operations of the profile, by access. The descriptor (`servers/purchasing.yaml`) declares the same set.
READS = (
    "search_suppliers",
    "get_supplier",
    "list_framework_agreements",
    "list_contracts",
    "get_contract",
)
WRITES = (
    "create_supplier",
    "update_supplier",
    "archive_supplier",
    "add_contract_note",
    "create_purchase_request",
    "cancel_purchase_request",
)
OPERATIONS = (*READS, *WRITES)

#: What `update_supplier` may change, and what `create_supplier` takes besides the name.
SUPPLIER_FIELDS = (
    "name",
    "registration_number",
    "vat_number",
    "country",
    "email",
    "phone",
    "website",
    "address_line",
    "city",
    "postal_code",
    "categories",
)
OPTIONAL_SUPPLIER_FIELDS = frozenset(SUPPLIER_FIELDS) - {"name", "country", "categories"}

# A name that says "bank detail", in any argument name or field name of a call.
_BANK_NAME = re.compile(
    "|".join(
        (
            "iban",
            "swift",
            "bank",
            "(^|[_ -])bic($|[_ -])",
            "account[_ -]?(number|no|holder)",
            "routing",
            "sort[_ -]?code",
            "(^|[_ -])bsb($|[_ -])",
            "payment[_ -]?(detail|instruction)",
        )
    ),
    re.IGNORECASE,
)
# A value shaped like an IBAN (two letters, two digits, 11 to 30 more characters), valid or not.
_IBAN_SHAPE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}(?:[ -]?[A-Za-z0-9]){11,30}(?![A-Za-z0-9])")


def refuse_bank_details(value: Any, where: str = "arguments") -> None:
    """Refuse a call that carries a bank detail: an argument or field named like one, or a text shaped like
    an IBAN. Payment data is a human process (core ADR 0198); the profile neither takes nor returns it."""
    if isinstance(value, dict):
        for key, item in value.items():
            if _BANK_NAME.search(str(key)):
                raise Refused(f"{where}: `{key}` is a bank detail; the profile never takes or returns payment data")
            refuse_bank_details(item, f"{where}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            refuse_bank_details(item, f"{where}[{index}]")
    elif isinstance(value, str) and _IBAN_SHAPE.search(value):
        raise Refused(f"{where}: the text holds an IBAN-shaped value; the profile never takes or returns payment data")


def encode_cursor(offset: int) -> str:
    """An opaque page cursor: callers hand it back as it came and never read it."""
    return base64.urlsafe_b64encode(f"o{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        if raw[0] == "o" and raw[1:].isdigit():
            return int(raw[1:])
    except (binascii.Error, UnicodeDecodeError, IndexError):
        pass
    raise Refused("cursor is not one this server gave; pass next_cursor back as it came")


def normalise_registration(value: str) -> str:
    """The form a registration number is compared in: no space, dot or dash, upper case."""
    return re.sub(r"[\s.\-/]", "", value).upper()


def normalise_name(value: str) -> str:
    """The form a supplier name is compared in for duplicates: letters and digits, lower case."""
    return re.sub(r"[^0-9a-z]", "", value.casefold())


def parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise Refused(f"{name} must be a date YYYY-MM-DD, got {value!r}") from error


def parse_amount(value: str, name: str, *, positive: bool) -> Decimal:
    """A decimal-point amount written as text; never a float (the arithmetic is the server's, exact)."""
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise Refused(f"{name} must be a decimal number written with a point, got {value!r}") from error
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise Refused(f"{name} must be {'greater than' if positive else 'at least'} zero")
    return amount


def money(amount: Decimal) -> str:
    return f"{amount.quantize(Decimal('0.01')):f}"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Supplier(Record):
    id: str
    name: str
    registration_number: str | None = None
    vat_number: str | None = None
    country: str
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    address_line: str | None = None
    city: str | None = None
    postal_code: str | None = None
    categories: list[str] = Field(default_factory=list)
    status: Literal["active", "archived"] = "active"
    archived_reason: str | None = None
    created_at: str
    updated_at: str


class Agreement(Record):
    id: str
    supplier_id: str
    title: str
    category: str
    valid_from: date
    valid_to: date | None = None
    currency: str
    terms_summary: str = ""


class Note(Record):
    id: str
    text: str
    created_at: str


class Contract(Record):
    id: str
    supplier_id: str
    title: str
    category: str
    start_date: date
    end_date: date | None = None
    currency: str
    value: str | None = None
    notes: list[Note] = Field(default_factory=list)


class RequestLine(Record):
    description: str
    quantity: str
    unit: str = "unit"
    unit_price: str
    line_total: str


class PurchaseRequest(Record):
    id: str
    supplier_id: str
    contract_id: str | None = None
    title: str
    lines: list[RequestLine]
    currency: str
    total: str
    needed_by: date | None = None
    justification: str = ""
    status: Literal["draft", "cancelled"] = "draft"
    cancel_reason: str | None = None
    created_at: str


class NewSupplier(Record):
    name: str
    country: str
    registration_number: str | None = None
    vat_number: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    address_line: str | None = None
    city: str | None = None
    postal_code: str | None = None
    categories: list[str] = Field(default_factory=list)


class NewPurchaseRequest(Record):
    supplier_id: str
    contract_id: str | None = None
    title: str
    lines: list[RequestLine]
    currency: str
    total: str
    needed_by: date | None = None
    justification: str = ""


class Page[T](Record):
    """One page of a listing: `next_cursor` is `None` on the last."""

    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def next_cursor(self) -> str | None:
        end = self.offset + len(self.items)
        return encode_cursor(end) if end < self.total else None
