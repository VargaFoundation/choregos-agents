# SPDX-License-Identifier: Apache-2.0
"""What a backend implements: the purchasing system behind the profile.

A backend maps its own records to the models of `profile.py` and raises `NotFound`, `Conflict` or
`BackendError`. It receives only validated, typed values: filters are fields, never a query string of its
system. It holds no idempotency of its own here: `tools.py` answers a replayed `action_key` before the
backend is called. A backend must neither take nor return a bank detail.

The only backend this version ships is the seeded fake (`fake.py`); a real one (Odoo) arrives with a later
version (PA-06) and implements this same protocol.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from ..profile import (
    Agreement,
    Contract,
    NewPurchaseRequest,
    NewSupplier,
    Note,
    Page,
    PurchaseRequest,
    Supplier,
)


class Backend(Protocol):
    name: str

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
        """Active suppliers (archived ones too when asked) matching every given filter, by name then id.
        `name` is a case-insensitive substring; `registration_number` and `country` match exactly."""
        ...

    async def get_supplier(self, supplier_id: str) -> Supplier: ...

    async def create_supplier(self, new: NewSupplier) -> Supplier: ...

    async def update_supplier(self, supplier_id: str, changes: dict[str, Any]) -> Supplier:
        """Apply `changes` (keys of `SUPPLIER_FIELDS`, `None` clears an optional one) and return the record."""
        ...

    async def archive_supplier(self, supplier_id: str, reason: str | None) -> Supplier: ...

    async def list_framework_agreements(
        self, *, category: str | None, valid_on: date | None, limit: int, offset: int
    ) -> Page[Agreement]:
        """Agreements of the category, valid on the date (`valid_from <= date`, no `valid_to` or
        `date <= valid_to`), by category then id."""
        ...

    async def list_contracts(
        self, *, ending_before: date | None, supplier_id: str | None, limit: int, offset: int
    ) -> Page[Contract]:
        """Contracts of the supplier whose end date is strictly before `ending_before` (a contract with no end
        date never is), earliest end first; every contract, by id, when no filter is given."""
        ...

    async def get_contract(self, contract_id: str) -> Contract: ...

    async def add_contract_note(self, contract_id: str, text: str) -> Note: ...

    async def create_purchase_request(self, new: NewPurchaseRequest) -> PurchaseRequest:
        """A request in the `draft` status: nothing is ordered."""
        ...

    async def get_purchase_request(self, request_id: str) -> PurchaseRequest: ...

    async def cancel_purchase_request(self, request_id: str, reason: str) -> PurchaseRequest: ...
