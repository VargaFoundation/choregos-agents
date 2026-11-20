# SPDX-License-Identifier: Apache-2.0
"""The seeded, in-memory purchasing system: the only backend this version serves.

`PURCHASING_FAKE=1` switches the server to it (`servers/purchasing.yaml`, `fake.env`); `PURCHASING_FAKE_SCENE`
picks the data it starts with. The companies, people and numbers are invented. The clock is fixed
(`FAKE_TODAY`) so a test and a demo read the same dates on any day.

Scenes:
- `default`: five suppliers (one archived), three contracts, three framework agreements, one draft purchase request.
- `duplicate-supplier`: the default, plus a second active record for the registration number of
  `SUP-0001` (a dirty source system), so a search finds two and a creation is refused as a duplicate.
- `expiring-contracts`: the default, plus four contracts ending within 90 days of `FAKE_TODAY`
  (and one that never ends, which no `ending_before` filter returns).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..errors import Conflict, NotFound
from ..profile import (
    Agreement,
    Contract,
    NewPurchaseRequest,
    NewSupplier,
    Note,
    Page,
    PurchaseRequest,
    Supplier,
    normalise_registration,
)

FAKE_TODAY = date(2026, 11, 20)
SCENES = ("default", "duplicate-supplier", "expiring-contracts")
_STAMP = "2026-11-20T09:00:00Z"


def scene_name(value: str) -> str:
    """`duplicate_supplier` and `duplicate-supplier` name the same scene (the descriptor's names have no underscore)."""
    scene = value.strip().lower().replace("_", "-")
    if scene not in SCENES:
        raise ValueError(f"PURCHASING_FAKE_SCENE must be one of {', '.join(SCENES)}, got {value!r}")
    return scene


def _supplier(number: int, name: str, country: str, registration: str | None, **more: Any) -> Supplier:
    return Supplier(
        id=f"SUP-{number:04d}",
        name=name,
        country=country,
        registration_number=registration,
        created_at=_STAMP,
        updated_at=_STAMP,
        **more,
    )


def _suppliers(scene: str) -> list[Supplier]:
    suppliers = [
        _supplier(
            1,
            "Brightwater Office Supplies Ltd",
            "GB",
            "09990101",
            vat_number="GB999000101",
            email="sales@brightwater.example",
            phone="+44 20 0000 0101",
            address_line="1 Example Row",
            city="Leeds",
            postal_code="LS1 0AA",
            categories=["office-supplies"],
        ),
        _supplier(
            2,
            "Kestrel Logistik GmbH",
            "DE",
            "HRB 990202",
            vat_number="DE999000202",
            email="orders@kestrel-logistik.example",
            address_line="2 Beispielweg",
            city="Hamburg",
            postal_code="20095",
            categories=["logistics"],
        ),
        _supplier(
            3,
            "Northfield Print Services SARL",
            "FR",
            "999 000 303",
            email="contact@northfield-print.example",
            city="Lyon",
            postal_code="69001",
            categories=["printing", "office-supplies"],
        ),
        _supplier(
            4,
            "Alder & Finch Facilities BV",
            "NL",
            "99000404",
            email="info@alder-finch.example",
            city="Utrecht",
            categories=["facilities"],
        ),
        _supplier(
            5,
            "Marrowdale Catering Co",
            "IE",
            "999505",
            city="Cork",
            categories=["catering"],
            status="archived",
            archived_reason="closed down",
        ),
    ]
    if scene == "duplicate-supplier":
        suppliers.append(
            _supplier(
                7,
                "Brightwater Office Supplies Limited",
                "GB",
                "0999 0101",
                email="accounts@brightwater.example",
                city="Leeds",
                categories=["office-supplies"],
            )
        )
    return suppliers


def _contract(
    number: int, supplier: int, title: str, category: str, start: date, end: date | None, value: str
) -> Contract:
    return Contract(
        id=f"CT-{number:04d}",
        supplier_id=f"SUP-{supplier:04d}",
        title=title,
        category=category,
        start_date=start,
        end_date=end,
        currency="EUR",
        value=value,
    )


def _contracts(scene: str) -> list[Contract]:
    contracts = [
        _contract(1, 1, "Office supplies, annual", "office-supplies", date(2025, 7, 1), date(2027, 6, 30), "48000.00"),
        _contract(2, 2, "Pallet transport, regional", "logistics", date(2024, 1, 1), date(2026, 12, 31), "120000.00"),
        _contract(3, 3, "Printing of brochures", "printing", date(2023, 10, 1), date(2026, 9, 30), "15000.00"),
    ]
    if scene == "expiring-contracts":
        contracts += [
            _contract(
                4, 4, "Cleaning of the main office", "facilities", date(2025, 1, 1), date(2026, 12, 5), "36000.00"
            ),
            _contract(
                5, 1, "Toner and paper, framework", "office-supplies", date(2024, 3, 1), date(2027, 1, 31), "9000.00"
            ),
            _contract(6, 2, "Courier service", "logistics", date(2024, 3, 1), date(2027, 2, 14), "22000.00"),
            _contract(7, 4, "Window maintenance", "facilities", date(2020, 1, 1), None, "4000.00"),
        ]
    return contracts


def _agreements() -> list[Agreement]:
    return [
        Agreement(
            id="FA-0001",
            supplier_id="SUP-0001",
            title="Office supplies framework",
            category="office-supplies",
            valid_from=date(2025, 1, 1),
            valid_to=date(2027, 12, 31),
            currency="EUR",
            terms_summary="Catalogue prices fixed for the year, delivery within three working days.",
        ),
        Agreement(
            id="FA-0002",
            supplier_id="SUP-0002",
            title="Regional logistics framework",
            category="logistics",
            valid_from=date(2024, 1, 1),
            valid_to=date(2026, 10, 31),
            currency="EUR",
            terms_summary="Per-pallet rates by zone.",
        ),
        Agreement(
            id="FA-0003",
            supplier_id="SUP-0004",
            title="Facilities services framework",
            category="facilities",
            valid_from=date(2026, 1, 1),
            valid_to=None,
            currency="EUR",
            terms_summary="Hourly rates, reviewed each January.",
        ),
    ]


def _page[T](items: list[T], limit: int, offset: int) -> Page[T]:
    return Page[T](items=items[offset : offset + limit], total=len(items), limit=limit, offset=offset)


class FakePurchasing:
    """A purchasing system in memory. One instance per process: its writes last as long as the process."""

    name = "fake"

    def __init__(self, scene: str = "default") -> None:
        self.scene = scene_name(scene)
        self.suppliers: dict[str, Supplier] = {s.id: s for s in _suppliers(self.scene)}
        self.contracts: dict[str, Contract] = {c.id: c for c in _contracts(self.scene)}
        self.agreements: dict[str, Agreement] = {a.id: a for a in _agreements()}
        self.requests: dict[str, PurchaseRequest] = {}
        self._supplier_seq = 100
        self._request_seq = 1
        self._note_seq = 0
        #: Every call that changed something, in order: what a test reads to count writes.
        self.writes: list[str] = []
        self._seed_request()

    def _seed_request(self) -> None:
        from ..profile import RequestLine

        self.requests["PR-0001"] = PurchaseRequest(
            id="PR-0001",
            supplier_id="SUP-0001",
            contract_id="CT-0001",
            title="Printer paper for the second floor",
            lines=[
                RequestLine(
                    description="A4 paper, 80 g, box of 5 reams",
                    quantity="10",
                    unit="box",
                    unit_price="21.50",
                    line_total="215.00",
                )
            ],
            currency="EUR",
            total="215.00",
            needed_by=date(2026, 12, 15),
            justification="Stock is at two boxes.",
            created_at=_STAMP,
        )

    # --- suppliers ------------------------------------------------------------------------------------

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
        wanted = normalise_registration(registration_number) if registration_number else None
        found = [
            s
            for s in self.suppliers.values()
            if (include_archived or s.status == "active")
            and (name is None or name.casefold() in s.name.casefold())
            and (wanted is None or (s.registration_number and normalise_registration(s.registration_number) == wanted))
            and (country is None or s.country == country)
        ]
        found.sort(key=lambda s: (s.name.casefold(), s.id))
        return _page(found, limit, offset)

    async def get_supplier(self, supplier_id: str) -> Supplier:
        try:
            return self.suppliers[supplier_id].model_copy(deep=True)
        except KeyError:
            raise NotFound(f"no supplier {supplier_id}") from None

    async def create_supplier(self, new: NewSupplier) -> Supplier:
        self._supplier_seq += 1
        supplier = Supplier(
            id=f"SUP-{self._supplier_seq:04d}", created_at=_STAMP, updated_at=_STAMP, **new.model_dump()
        )
        self.suppliers[supplier.id] = supplier
        self.writes.append(f"create_supplier {supplier.id}")
        return supplier.model_copy(deep=True)

    async def update_supplier(self, supplier_id: str, changes: dict[str, Any]) -> Supplier:
        current = await self.get_supplier(supplier_id)
        updated = current.model_copy(update={**changes, "updated_at": _STAMP})
        self.suppliers[supplier_id] = updated
        self.writes.append(f"update_supplier {supplier_id}")
        return updated.model_copy(deep=True)

    async def archive_supplier(self, supplier_id: str, reason: str | None) -> Supplier:
        current = await self.get_supplier(supplier_id)
        archived = current.model_copy(update={"status": "archived", "archived_reason": reason, "updated_at": _STAMP})
        self.suppliers[supplier_id] = archived
        self.writes.append(f"archive_supplier {supplier_id}")
        return archived.model_copy(deep=True)

    # --- agreements and contracts -----------------------------------------------------------------------

    async def list_framework_agreements(
        self, *, category: str | None, valid_on: date | None, limit: int, offset: int
    ) -> Page[Agreement]:
        found = [
            a
            for a in self.agreements.values()
            if (category is None or a.category == category)
            and (valid_on is None or (a.valid_from <= valid_on and (a.valid_to is None or valid_on <= a.valid_to)))
        ]
        found.sort(key=lambda a: (a.category, a.id))
        return _page([a.model_copy(deep=True) for a in found], limit, offset)

    async def list_contracts(
        self, *, ending_before: date | None, supplier_id: str | None, limit: int, offset: int
    ) -> Page[Contract]:
        found = [
            c
            for c in self.contracts.values()
            if (supplier_id is None or c.supplier_id == supplier_id)
            and (ending_before is None or (c.end_date is not None and c.end_date < ending_before))
        ]
        found.sort(key=lambda c: (c.end_date or date.max, c.id) if ending_before else (date.min, c.id))
        return _page([c.model_copy(deep=True) for c in found], limit, offset)

    async def get_contract(self, contract_id: str) -> Contract:
        try:
            return self.contracts[contract_id].model_copy(deep=True)
        except KeyError:
            raise NotFound(f"no contract {contract_id}") from None

    async def add_contract_note(self, contract_id: str, text: str) -> Note:
        contract = await self.get_contract(contract_id)
        self._note_seq += 1
        note = Note(id=f"NOTE-{self._note_seq:04d}", text=text, created_at=_STAMP)
        self.contracts[contract.id].notes.append(note)
        self.writes.append(f"add_contract_note {contract_id}")
        return note.model_copy(deep=True)

    # --- purchase requests ----------------------------------------------------------------------------

    async def create_purchase_request(self, new: NewPurchaseRequest) -> PurchaseRequest:
        self._request_seq += 1
        request = PurchaseRequest(id=f"PR-{self._request_seq:04d}", created_at=_STAMP, **new.model_dump())
        self.requests[request.id] = request
        self.writes.append(f"create_purchase_request {request.id}")
        return request.model_copy(deep=True)

    async def get_purchase_request(self, request_id: str) -> PurchaseRequest:
        try:
            return self.requests[request_id].model_copy(deep=True)
        except KeyError:
            raise NotFound(f"no purchase request {request_id}") from None

    async def cancel_purchase_request(self, request_id: str, reason: str) -> PurchaseRequest:
        current = await self.get_purchase_request(request_id)
        if current.status == "cancelled":
            raise Conflict(f"purchase request {request_id} is already cancelled")
        cancelled = current.model_copy(update={"status": "cancelled", "cancel_reason": reason})
        self.requests[request_id] = cancelled
        self.writes.append(f"cancel_purchase_request {request_id}")
        return cancelled.model_copy(deep=True)
