# SPDX-License-Identifier: Apache-2.0
"""What a backend implements: the purchasing system behind the profile.

A backend maps its own records to the models of `profile.py` and raises `NotFound`, `Conflict` or
`BackendError`. It receives only validated, typed values: filters are fields, never a query string of its
system. It holds no idempotency of its own here: `tools.py` answers a replayed `action_key` before the
backend is called. A backend must neither take nor return a bank detail.

Two backends ship: the seeded fake (`fake.py`) and Odoo (`odoo.py`, PA-06). A backend raises `NotSupported`
for what its system cannot serve honestly, and never approximates.

**Idempotence across restarts.** The three writes whose replay would create a second record (`create_supplier`,
`add_contract_note`, `create_purchase_request`) receive the platform's `action_key`. A backend whose system can
hold a reference stores one with the record and looks it up before writing, so a replay after a restart returns
the first record. The fake keeps no such memory: it lives and dies with the process.
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

    async def create_supplier(self, new: NewSupplier, *, action_key: str | None = None) -> Supplier: ...

    async def supplier_created_by(self, action_key: str) -> Supplier | None:
        """The supplier an earlier `create_supplier` made under this `action_key`, or `None`: the answer to a
        replay after a restart, asked before the duplicate check (which would find that very record)."""
        ...

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

    async def add_contract_note(self, contract_id: str, text: str, *, action_key: str | None = None) -> Note: ...

    async def create_purchase_request(
        self, new: NewPurchaseRequest, *, action_key: str | None = None
    ) -> PurchaseRequest:
        """A request in the `draft` status: nothing is ordered."""
        ...

    async def get_purchase_request(self, request_id: str) -> PurchaseRequest: ...

    async def cancel_purchase_request(self, request_id: str, reason: str) -> PurchaseRequest: ...
