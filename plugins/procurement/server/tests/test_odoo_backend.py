# SPDX-License-Identifier: Apache-2.0
"""The Odoo backend on hand-written JSON-2 exchanges (`odoo_script.py`): each mapped operation, its error
paths and its paging; the operations and fields marked not supported; the idempotence that survives a restart;
what is never sent (a bank detail, a caller's text as a query) and never shown (the key, a traceback)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
import pytest
import respx

from tranibor_purchasing.backends.odoo import MAX_ANSWER, MAX_CANDIDATES, OdooPurchasing
from tranibor_purchasing.config import Settings
from tranibor_purchasing.errors import BackendError, Conflict, NotFound, NotSupported, Refused
from tranibor_purchasing.profile import NewPurchaseRequest, NewSupplier, RequestLine
from tranibor_purchasing.tools import PurchasingTools

from .odoo_script import KEY, ODOO_ENV, Odoo, has_registry, no_registry, one_supplier_row

TAGS = [{"id": 3, "name": "Office Supplies"}]


def domain_of(call: dict[str, Any]) -> list[Any]:
    return list(call["domain"])


# ─────────────────────────── the transport ───────────────────────────


async def test_every_call_is_a_json2_post_with_the_key_and_the_database(odoo: Odoo) -> None:
    # create_supplier asks the country, creates, then asks Odoo its version and reads the record back
    odoo.script.expect("res.country", "search_read", [{"id": 75, "code": "FR"}])
    odoo.script.expect("res.partner", "create", [11])
    has_registry(odoo.script)
    odoo.script.expect("res.partner", "search_read", [one_supplier_row(11, category_id=[])])
    await odoo.backend.create_supplier(NewSupplier(name="Quillfeather Stationery SAS", country="FR"))
    first = odoo.script.calls[0]
    assert first.headers["authorization"] == f"bearer {KEY}"
    assert first.headers["x-odoo-database"] == "acme"
    assert first.headers["content-type"].startswith("application/json")
    assert first.headers["user-agent"].startswith("tranibor-purchasing/")
    assert {c.headers["authorization"] for c in odoo.script.calls} == {f"bearer {KEY}"}


@pytest.mark.parametrize(
    ("status", "body", "expected", "words"),
    [
        (
            401,
            {"name": "odoo.exceptions.AccessDenied", "message": "Invalid apikey"},
            BackendError,
            "API key is invalid",
        ),
        (
            403,
            {"name": "odoo.exceptions.AccessError", "message": "You are not allowed"},
            BackendError,
            "lacks the right",
        ),
        (404, {"name": "odoo.exceptions.MissingError", "message": "Record does not exist"}, NotFound, "does not exist"),
        (404, {"name": "werkzeug.exceptions.NotFound", "message": "the model 'x' does not exist"}, BackendError, "404"),
        (422, {"name": "odoo.exceptions.ValidationError", "message": "The VAT number is invalid"}, BackendError, "VAT"),
    ],
)
async def test_odoo_errors_become_this_servers_and_never_show_the_traceback_or_the_key(
    odoo: Odoo, status: int, body: dict[str, str], expected: type[Exception], words: str
) -> None:
    odoo.script.expect(
        "res.currency", "search_read", {**body, "debug": "Traceback ... secret frame ..."}, status=status
    )
    with pytest.raises(expected) as raised:
        await odoo.backend.create_purchase_request(_request("1"))
    shown = str(raised.value)
    assert words in shown
    assert "Traceback" not in shown and "secret frame" not in shown and KEY not in shown


def _bare() -> OdooPurchasing:
    return OdooPurchasing.from_settings(Settings.from_env(ODOO_ENV))


async def test_a_timeout_a_dead_host_and_a_non_json_answer_are_backend_errors() -> None:
    backend = _bare()
    with respx.mock(assert_all_called=False) as router:
        route = router.route(host="odoo.example.test")
        route.mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(BackendError, match=r"did not answer res\.currency\.search_read in time"):
            await backend._currency_id("EUR")
        route.mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(BackendError, match="could not be reached"):
            await backend._currency_id("EUR")
        route.mock(return_value=httpx.Response(200, text="<html>login</html>"))
        with pytest.raises(BackendError, match="not JSON"):
            await backend._currency_id("EUR")


async def test_a_redirect_is_not_followed_and_an_oversized_answer_is_refused() -> None:
    backend = _bare()
    with respx.mock(assert_all_called=False) as router:
        elsewhere = router.get("https://evil.example.test/").mock(return_value=httpx.Response(200, json=[]))
        route = router.route(host="odoo.example.test")
        route.mock(return_value=httpx.Response(302, headers={"Location": "https://evil.example.test/"}))
        with pytest.raises(BackendError, match="redirects are not followed"):
            await backend._currency_id("EUR")
        assert not elsewhere.called
        route.mock(return_value=httpx.Response(200, content=b"[" + b" " * MAX_ANSWER + b"]"))
        with pytest.raises(BackendError, match="larger than"):
            await backend._currency_id("EUR")


# ─────────────────────────── suppliers: reads ───────────────────────────


async def test_search_suppliers_builds_its_domain_from_typed_values_and_pages(odoo: Odoo) -> None:
    hostile = "x') or ('1','=','1"
    has_registry(odoo.script)
    odoo.script.expect("res.partner", "search_count", 41)
    odoo.script.expect("res.partner", "search_read", [one_supplier_row(7)])
    odoo.script.expect("res.partner.category", "read", TAGS)
    page = await odoo.backend.search_suppliers(
        name=hostile, registration_number=None, country="GB", include_archived=True, limit=1, offset=40
    )
    count, read = odoo.script.sent("res.partner", "search_count")[0], odoo.script.sent("res.partner", "search_read")[0]
    assert (
        domain_of(count)
        == domain_of(read)
        == [
            ["supplier_rank", ">", 0],
            ["parent_id", "=", False],
            ["active", "in", [True, False]],
            ["name", "ilike", hostile],  # the caller's text is one value, nothing more
            ["country_id.code", "=", "GB"],
        ]
    )
    assert (read["limit"], read["offset"], read["order"]) == (1, 40, "name asc, id asc")
    assert page.total == 41 and page.next_cursor is None  # offset 40 + 1 row = 41: the last page
    [supplier] = page.items
    assert (supplier.id, supplier.country, supplier.categories, supplier.status) == (
        "7",
        "GB",
        ["office-supplies"],
        "active",
    )
    assert (supplier.registration_number, supplier.vat_number, supplier.address_line, supplier.postal_code) == (
        "0999 0101",
        "GB999000101",
        "1 Example Row",
        "LS1 0AA",
    )
    assert supplier.phone is None and supplier.created_at == "2026-11-20T09:00:00Z"


async def test_search_suppliers_not_the_last_page_has_a_cursor_and_archived_ones_are_hidden_by_default(
    odoo: Odoo,
) -> None:
    has_registry(odoo.script)
    odoo.script.expect("res.partner", "search_count", 120)
    odoo.script.expect(
        "res.partner", "search_read", [one_supplier_row(7, category_id=[]), one_supplier_row(8, category_id=[])]
    )
    page = await odoo.backend.search_suppliers(
        name=None, registration_number=None, country=None, include_archived=False, limit=2, offset=0
    )
    assert page.next_cursor is not None and page.total == 120
    assert ["active", "in", [True, False]] not in domain_of(odoo.script.sent("res.partner", "search_read")[0])


async def test_a_registration_number_is_compared_ignoring_spaces_dots_and_dashes(odoo: Odoo) -> None:
    has_registry(odoo.script)
    candidates = [
        one_supplier_row(8, name="Zed Print", company_registry="999.000-303", category_id=[]),
        one_supplier_row(7, name="Alder Print", company_registry="999 000 303", category_id=[]),
        one_supplier_row(9, name="Not Print", company_registry="9990003039", category_id=[]),  # looser, not equal
    ]
    odoo.script.expect("res.partner", "search_read", candidates)
    page = await odoo.backend.search_suppliers(
        name=None, registration_number="999-000.303", country=None, include_archived=False, limit=1, offset=0
    )
    sent = odoo.script.sent("res.partner", "search_read")[0]
    assert domain_of(sent)[-1] == ["company_registry", "=ilike", "%9%9%9%0%0%0%3%0%3%"]
    assert [s.name for s in page.items] == ["Alder Print"] and page.total == 2 and page.next_cursor is not None


async def test_too_many_candidates_for_a_registration_number_is_an_error_not_a_silent_cut(odoo: Odoo) -> None:
    has_registry(odoo.script)
    rows = [one_supplier_row(n, category_id=[]) for n in range(1, MAX_CANDIDATES + 2)]
    odoo.script.expect("res.partner", "search_read", rows)
    with pytest.raises(BackendError, match="narrow the search"):
        await odoo.backend.search_suppliers(
            name=None, registration_number="0999 0101", country=None, include_archived=False, limit=10, offset=0
        )


async def test_on_an_odoo_without_company_registry_the_registration_number_is_not_supported(odoo: Odoo) -> None:
    no_registry(odoo.script)
    with pytest.raises(NotSupported, match="registration_number is not supported by this backend"):
        await odoo.backend.search_suppliers(
            name=None, registration_number="0999 0101", country=None, include_archived=False, limit=10, offset=0
        )
    assert odoo.script.sent("res.partner", "search_read") == []  # nothing was searched on a field Odoo lacks


async def test_without_company_registry_a_supplier_is_read_without_one_and_the_field_is_not_requested(
    odoo: Odoo,
) -> None:
    no_registry(odoo.script)
    odoo.script.expect("res.partner", "search_read", [one_supplier_row(7, category_id=[], company_registry="ignored")])
    supplier = await odoo.backend.get_supplier("7")
    assert supplier.registration_number is None
    assert "company_registry" not in odoo.script.sent("res.partner", "search_read")[0]["fields"]


async def test_get_supplier(odoo: Odoo) -> None:
    has_registry(odoo.script)
    odoo.script.expect("res.partner", "search_read", [])
    with pytest.raises(NotFound, match="no supplier 99"):
        await odoo.backend.get_supplier("99")
    assert domain_of(odoo.script.sent("res.partner", "search_read")[0])[0] == ["id", "=", 99]
    # an id that cannot be one never reaches Odoo
    for bad in ("abc", "0", "7; drop", "1" * 13):
        with pytest.raises(NotFound):
            await odoo.backend.get_supplier(bad)
    assert len(odoo.script.sent("res.partner", "search_read")) == 1


# ─────────────────────────── suppliers: writes ───────────────────────────


async def test_create_supplier_maps_the_fields_and_stores_a_reference_of_the_action(odoo: Odoo) -> None:
    has_registry(odoo.script)  # asked first, for the registration number
    odoo.script.expect("res.country", "search_read", [{"id": 75, "code": "FR"}])
    odoo.script.expect("res.partner.category", "search_read", [{"id": 3, "name": "Office Supplies"}])
    odoo.script.expect("res.partner.category", "create", [4])
    odoo.script.expect("res.partner", "create", [11])
    odoo.script.expect(
        "res.partner", "search_read", [one_supplier_row(11, name="Quillfeather Stationery SAS", category_id=[3, 4])]
    )
    odoo.script.expect("res.partner.category", "read", [*TAGS, {"id": 4, "name": "printing"}])
    created = await odoo.backend.create_supplier(
        NewSupplier(
            name="Quillfeather Stationery SAS",
            country="fr".upper(),
            registration_number="999 000 808",
            vat_number="FR99999000808",
            email="hello@quillfeather.example",
            address_line="2 Rue Exemple",
            city="Lyon",
            postal_code="69001",
            categories=["office-supplies", "printing"],
        ),
        action_key="action-1",
    )
    [values] = odoo.script.sent("res.partner", "create")[0]["vals_list"]
    marker = values["comment"]
    assert marker.startswith("<p>Created through the purchasing integration (tranibor-") and "action-1" not in marker
    assert values == {
        "name": "Quillfeather Stationery SAS",
        "country_id": 75,
        "company_registry": "999 000 808",
        "vat": "FR99999000808",
        "email": "hello@quillfeather.example",
        "street": "2 Rue Exemple",
        "city": "Lyon",
        "zip": "69001",
        "category_id": [[6, 0, [3, 4]]],
        "supplier_rank": 1,
        "comment": marker,
    }
    assert odoo.script.sent("res.partner.category", "create") == [{"vals_list": [{"name": "printing"}]}]
    assert created.id == "11" and created.categories == ["office-supplies", "printing"]


async def test_create_supplier_on_an_odoo_without_company_registry_refuses_a_registration_number_and_writes_nothing(
    odoo: Odoo,
) -> None:
    no_registry(odoo.script)
    with pytest.raises(NotSupported, match="registration_number"):
        await odoo.backend.create_supplier(NewSupplier(name="X", country="FR", registration_number="999 000 808"))
    assert odoo.script.sent("res.partner", "create") == []


async def test_an_unknown_country_is_refused_before_anything_is_written(odoo: Odoo) -> None:
    odoo.script.expect("res.country", "search_read", [])
    with pytest.raises(Refused, match="ZZ is not a country"):
        await odoo.backend.create_supplier(NewSupplier(name="X", country="ZZ"))
    assert odoo.script.sent("res.partner", "create") == []


async def test_update_supplier_writes_odoo_columns_and_clears_with_false(odoo: Odoo) -> None:
    has_registry(odoo.script)
    odoo.script.expect("res.partner", "search_read", [one_supplier_row(7, category_id=[])])
    odoo.script.expect("res.partner", "write", True)
    odoo.script.expect(
        "res.partner", "search_read", [one_supplier_row(7, category_id=[], phone="+44 20 0000 0101", street=False)]
    )
    updated = await odoo.backend.update_supplier(
        "7", {"phone": "+44 20 0000 0101", "address_line": None, "postal_code": "LS2 0BB", "vat_number": "GB1"}
    )
    assert odoo.script.sent("res.partner", "write") == [
        {"ids": [7], "vals": {"phone": "+44 20 0000 0101", "street": False, "zip": "LS2 0BB", "vat": "GB1"}}
    ]
    assert updated.phone == "+44 20 0000 0101" and updated.address_line is None


async def test_archive_supplier_sets_active_false_and_notes_the_reason_in_the_chatter(odoo: Odoo) -> None:
    has_registry(odoo.script)
    odoo.script.expect("res.partner", "search_read", [one_supplier_row(7, category_id=[])])
    odoo.script.expect("res.partner", "write", True)
    odoo.script.expect("res.partner", "message_post", [501])
    odoo.script.expect("res.partner", "search_read", [one_supplier_row(7, category_id=[], active=False)])
    archived = await odoo.backend.archive_supplier("7", "Merged into another supplier")
    assert odoo.script.sent("res.partner", "write") == [{"ids": [7], "vals": {"active": False}}]
    [post] = odoo.script.sent("res.partner", "message_post")
    assert (
        post["ids"] == [7]
        and post["body"] == "Archived through the purchasing integration. Reason: Merged into another supplier"
    )
    assert (post["message_type"], post["subtype_xmlid"]) == ("comment", "mail.mt_note")
    assert archived.status == "archived" and archived.archived_reason is None


# ─────────────────────────── agreements and contracts ───────────────────────────

AGREEMENT = {
    "id": 21,
    "name": "BO00021",
    "reference": "Office supplies framework",
    "vendor_id": [7, "Brightwater Office Supplies Ltd"],
    "date_start": "2025-01-01",
    "date_end": "2027-12-31",
    "currency_id": [1, "EUR"],
    "description": "<p>Catalogue prices fixed &amp; delivery in <b>three</b> days.</p>",
}
IN_FORCE = [
    ["requisition_type", "=", "blanket_order"],
    ["state", "in", ["confirmed", "done"]],
    ["vendor_id", "!=", False],
    ["date_start", "!=", False],
]


async def test_agreements_are_blanket_orders_in_force_and_carry_no_category(odoo: Odoo) -> None:
    odoo.script.expect("purchase.requisition", "search_count", 3)
    odoo.script.expect("purchase.requisition", "search_read", [AGREEMENT])
    page = await odoo.backend.list_framework_agreements(category=None, valid_on=None, limit=1, offset=2)
    assert domain_of(odoo.script.sent("purchase.requisition", "search_read")[0]) == IN_FORCE
    sent = odoo.script.sent("purchase.requisition", "search_read")[0]
    assert (sent["order"], sent["limit"], sent["offset"]) == ("id asc", 1, 2)
    [agreement] = page.items
    assert agreement.model_dump(mode="json") == {
        "id": "21",
        "supplier_id": "7",
        "title": "Office supplies framework",
        "category": "",
        "valid_from": "2025-01-01",
        "valid_to": "2027-12-31",
        "currency": "EUR",
        "terms_summary": "Catalogue prices fixed & delivery in three days.",
    }
    assert page.total == 3 and page.next_cursor is None


async def test_agreements_valid_on_a_date_are_confirmed_started_and_not_ended(odoo: Odoo) -> None:
    odoo.script.expect("purchase.requisition", "search_count", 0)
    odoo.script.expect("purchase.requisition", "search_read", [])
    page = await odoo.backend.list_framework_agreements(category=None, valid_on=date(2026, 11, 20), limit=10, offset=0)
    assert page.items == [] and page.total == 0
    assert domain_of(odoo.script.sent("purchase.requisition", "search_read")[0]) == [
        *IN_FORCE,
        ["state", "=", "confirmed"],
        ["date_start", "<=", "2026-11-20"],
        "|",
        ["date_end", "=", False],
        ["date_end", ">=", "2026-11-20"],
    ]


async def test_the_category_filter_is_not_supported_and_nothing_is_asked_of_odoo(odoo: Odoo) -> None:
    with pytest.raises(NotSupported, match="category filter is not supported by this backend"):
        await odoo.backend.list_framework_agreements(category="office-supplies", valid_on=None, limit=10, offset=0)
    assert odoo.script.calls == []


async def test_an_agreement_without_a_vendor_or_start_is_a_backend_error_if_odoo_returns_one(odoo: Odoo) -> None:
    odoo.script.expect("purchase.requisition", "search_count", 1)
    odoo.script.expect("purchase.requisition", "search_read", [{**AGREEMENT, "vendor_id": False}])
    with pytest.raises(BackendError, match="lacks a vendor"):
        await odoo.backend.list_framework_agreements(category=None, valid_on=None, limit=10, offset=0)


async def test_contracts_ending_before_a_date_come_earliest_first_with_a_note_count(odoo: Odoo) -> None:
    odoo.script.expect("purchase.requisition", "search_count", 2)
    odoo.script.expect("purchase.requisition", "search_read", [AGREEMENT])
    odoo.script.expect(
        "mail.message",
        "search_read",
        [
            {"id": 90, "res_id": 21, "date": "2026-05-01 10:00:00"},
            {"id": 91, "res_id": 21, "date": "2026-06-01 10:00:00"},
        ],
    )
    page = await odoo.backend.list_contracts(ending_before=date(2028, 1, 1), supplier_id="7", limit=5, offset=0)
    sent = odoo.script.sent("purchase.requisition", "search_read")[0]
    assert domain_of(sent) == [
        *IN_FORCE,
        ["vendor_id", "=", 7],
        ["date_end", "!=", False],
        ["date_end", "<", "2028-01-01"],
    ]
    assert sent["order"] == "date_end asc, id asc"
    [contract] = page.items
    assert len(contract.notes) == 2 and contract.value is None and contract.category == ""
    assert (contract.id, contract.supplier_id, contract.start_date, contract.end_date) == (
        "21",
        "7",
        date(2025, 1, 1),
        date(2027, 12, 31),
    )
    notes_domain = domain_of(odoo.script.sent("mail.message", "search_read")[0])
    assert notes_domain == [
        ["model", "=", "purchase.requisition"],
        ["res_id", "in", [21]],
        ["message_type", "=", "comment"],
    ]
    assert "body" not in odoo.script.sent("mail.message", "search_read")[0]["fields"]  # a count needs no text


async def test_contracts_of_a_supplier_that_cannot_exist_are_an_empty_page_and_ask_nothing(odoo: Odoo) -> None:
    page = await odoo.backend.list_contracts(ending_before=None, supplier_id="SUP-1", limit=5, offset=0)
    assert page.items == [] and page.total == 0 and odoo.script.calls == []


async def test_get_contract_reads_its_notes_as_text(odoo: Odoo) -> None:
    odoo.script.expect("purchase.requisition", "search_read", [AGREEMENT])
    odoo.script.expect(
        "mail.message",
        "search_read",
        [{"id": 90, "res_id": 21, "body": "<p>Reviewed &lt;ok&gt;</p>", "date": "2026-05-01 10:00:00"}],
    )
    contract = await odoo.backend.get_contract("21")
    [note] = contract.notes
    assert (note.id, note.text, note.created_at) == ("90", "Reviewed <ok>", "2026-05-01T10:00:00Z")
    odoo.script.expect("purchase.requisition", "search_read", [])
    with pytest.raises(NotFound, match="no contract 22"):
        await odoo.backend.get_contract("22")


async def test_add_contract_note_posts_an_internal_note_with_the_actions_message_id(odoo: Odoo) -> None:
    odoo.script.expect("purchase.requisition", "search_read", [AGREEMENT])
    odoo.script.expect("mail.message", "search_read", [])  # no message with this reference yet
    odoo.script.expect("purchase.requisition", "message_post", [92])
    odoo.script.expect(
        "mail.message", "search_read", [{"id": 92, "body": "<p>Reviewed.</p>", "date": "2026-11-20 11:00:00"}]
    )
    note = await odoo.backend.add_contract_note("21", "Reviewed.", action_key="action-9")
    [post] = odoo.script.sent("purchase.requisition", "message_post")
    assert post["ids"] == [21] and post["body"] == "Reviewed." and post["subtype_xmlid"] == "mail.mt_note"
    assert post["message_id"].startswith("<tranibor-") and post["message_id"].endswith("@purchasing.invalid>")
    assert "action-9" not in post["message_id"]
    assert domain_of(odoo.script.sent("mail.message", "search_read")[0]) == [["message_id", "=", post["message_id"]]]
    assert (note.id, note.text) == ("92", "Reviewed.")


# ─────────────────────────── purchase requests ───────────────────────────


def _request(supplier: str = "7", **more: Any) -> NewPurchaseRequest:
    base: dict[str, Any] = {
        "supplier_id": supplier,
        "title": "Paper & toner <urgent>",
        "lines": [
            RequestLine(description="A4 paper", quantity="10", unit="box", unit_price="21.50", line_total="215.00"),
            RequestLine(description="Toner", quantity="2.5", unit="unit", unit_price="40.00", line_total="100.00"),
        ],
        "currency": "EUR",
        "total": "315.00",
        "needed_by": date(2026, 12, 15),
        "justification": "Stock is low.",
        **more,
    }
    return NewPurchaseRequest(**base)


ORDER_ROW = {
    "id": 31,
    "partner_id": [7, "Brightwater Office Supplies Ltd"],
    "requisition_id": [21, "BO00021"],
    "note": "<p>Paper &amp; toner &lt;urgent&gt;</p><p>Stock is low.</p>",
    "currency_id": [1, "EUR"],
    "state": "draft",
    "date_planned": "2026-12-15 12:00:00",
    "create_date": "2026-11-20 09:00:00",
}
ORDER_LINES = [
    {"id": 1, "name": "A4 paper [unit: box]", "product_qty": 10.0, "price_unit": 21.5},
    {"id": 2, "name": "Toner", "product_qty": 2.5, "price_unit": 40.0},
]


async def test_create_purchase_request_writes_a_draft_order_with_its_lines_and_a_reference(odoo: Odoo) -> None:
    odoo.script.expect("purchase.order", "search_read", [])  # no order carries this action's reference
    odoo.script.expect("res.currency", "search_read", [{"id": 1, "name": "EUR"}])
    odoo.script.expect("purchase.order", "create", [31])
    odoo.script.expect("purchase.order", "search_read", [ORDER_ROW])
    odoo.script.expect("purchase.order.line", "search_read", ORDER_LINES)
    request = await odoo.backend.create_purchase_request(_request(contract_id="21"), action_key="action-5")
    [values] = odoo.script.sent("purchase.order", "create")[0]["vals_list"]
    marker = values["origin"]
    assert marker.startswith("tranibor-") and "action-5" not in marker
    assert values["partner_id"] == 7 and values["requisition_id"] == 21 and values["currency_id"] == 1
    assert values["note"] == "<p>Paper &amp; toner &lt;urgent&gt;</p><p>Stock is low.</p>"  # escaped, never raw HTML
    assert values["order_line"] == [
        [
            0,
            0,
            {
                "name": "A4 paper [unit: box]",
                "product_qty": 10.0,
                "price_unit": 21.5,
                "date_planned": "2026-12-15 12:00:00",
            },
        ],
        [0, 0, {"name": "Toner", "product_qty": 2.5, "price_unit": 40.0, "date_planned": "2026-12-15 12:00:00"}],
    ]
    assert domain_of(odoo.script.sent("purchase.order", "search_read")[0]) == [["origin", "=", marker]]
    assert request.model_dump(mode="json") == {
        "id": "31",
        "supplier_id": "7",
        "contract_id": "21",
        "title": "Paper & toner <urgent>",
        "lines": [
            {"description": "A4 paper", "quantity": "10", "unit": "box", "unit_price": "21.50", "line_total": "215.00"},
            {"description": "Toner", "quantity": "2.5", "unit": "unit", "unit_price": "40.00", "line_total": "100.00"},
        ],
        "currency": "EUR",
        "total": "315.00",
        "needed_by": "2026-12-15",
        "justification": "Stock is low.",
        "status": "draft",
        "cancel_reason": None,
        "created_at": "2026-11-20T09:00:00Z",
    }


async def test_a_replayed_create_returns_the_order_that_carries_the_reference_and_writes_nothing(odoo: Odoo) -> None:
    odoo.script.expect("purchase.order", "search_read", [ORDER_ROW])
    odoo.script.expect("purchase.order.line", "search_read", ORDER_LINES)
    request = await odoo.backend.create_purchase_request(_request(contract_id="21"), action_key="action-5")
    assert request.id == "31" and odoo.script.sent("purchase.order", "create") == []


async def test_a_currency_odoo_does_not_have_and_too_fine_a_quantity_are_refused_before_a_write(odoo: Odoo) -> None:
    odoo.script.expect("purchase.order", "search_read", [])
    odoo.script.expect("res.currency", "search_read", [])
    with pytest.raises(Refused, match="currency EUR is not an active currency"):
        await odoo.backend.create_purchase_request(_request(), action_key="k")
    fine = _request(
        lines=[RequestLine(description="Bolts", quantity="0.125", unit="unit", unit_price="1.00", line_total="0.13")],
        total="0.13",
    )
    odoo.script.expect("purchase.order", "search_read", [])
    with pytest.raises(Refused, match="more than two decimals"):
        await odoo.backend.create_purchase_request(fine, action_key="k2")
    assert odoo.script.sent("purchase.order", "create") == []


async def test_amounts_odoo_stored_differently_are_cancelled_and_reported(odoo: Odoo) -> None:
    rounded = [{**ORDER_LINES[0], "price_unit": 22.0}, ORDER_LINES[1]]  # a database keeping fewer decimals
    odoo.script.expect("purchase.order", "search_read", [])
    odoo.script.expect("res.currency", "search_read", [{"id": 1, "name": "EUR"}])
    odoo.script.expect("purchase.order", "create", [31])
    odoo.script.expect("purchase.order", "search_read", [ORDER_ROW])
    odoo.script.expect("purchase.order.line", "search_read", rounded)
    odoo.script.expect("purchase.order", "button_cancel", None)
    with pytest.raises(BackendError, match=r"stored other amounts than asked.*cancelled"):
        await odoo.backend.create_purchase_request(_request(), action_key="k")
    assert odoo.script.sent("purchase.order", "button_cancel") == [{"ids": [31]}]


async def test_a_purchase_order_that_is_no_longer_a_draft_is_a_conflict(odoo: Odoo) -> None:
    odoo.script.expect("purchase.order", "search_read", [{**ORDER_ROW, "state": "purchase"}])
    with pytest.raises(Conflict, match="no longer a draft request"):
        await odoo.backend.get_purchase_request("31")
    with pytest.raises(NotFound):
        await odoo.backend.get_purchase_request("x1")


async def test_cancel_runs_odoos_own_cancel_for_a_draft_and_notes_the_reason(odoo: Odoo) -> None:
    odoo.script.expect("purchase.order", "search_read", [ORDER_ROW])
    odoo.script.expect("purchase.order.line", "search_read", ORDER_LINES)
    odoo.script.expect("purchase.order", "button_cancel", None)
    odoo.script.expect("purchase.order", "message_post", [600])
    odoo.script.expect("purchase.order", "search_read", [{**ORDER_ROW, "state": "cancel"}])
    odoo.script.expect("purchase.order.line", "search_read", ORDER_LINES)
    cancelled = await odoo.backend.cancel_purchase_request("31", "Not needed")
    assert cancelled.status == "cancelled"
    assert odoo.script.sent("purchase.order", "button_cancel") == [{"ids": [31]}]
    assert "Reason: Not needed" in odoo.script.sent("purchase.order", "message_post")[0]["body"]


# ─────────────────────────── what is never sent ───────────────────────────


async def test_no_request_of_a_whole_session_names_a_bank_or_reads_without_a_field_list(odoo: Odoo) -> None:
    has_registry(odoo.script)
    odoo.script.expect("res.partner", "search_count", 1)
    odoo.script.expect("res.partner", "search_read", [one_supplier_row(7)])
    odoo.script.expect("res.partner.category", "read", TAGS)
    await odoo.backend.search_suppliers(
        name="Bright", registration_number=None, country=None, include_archived=False, limit=5, offset=0
    )
    odoo.script.expect("res.partner", "search_read", [one_supplier_row(7)])
    odoo.script.expect("res.partner.category", "read", TAGS)
    await odoo.backend.get_supplier("7")
    for call in odoo.script.calls:
        assert "bank" not in json.dumps(call.body).lower() and "iban" not in json.dumps(call.body).lower()
        if call.method in ("search_read", "read"):
            assert call.body.get("fields"), f"{call.model}.{call.method} reads every field Odoo allows"


# ─────────────────────────── through the server: restarts, errors, config ───────────────────────────


def _tools(odoo: Odoo) -> PurchasingTools:
    return PurchasingTools(odoo.backend)  # a new process: its own memory is empty


async def test_a_create_replayed_after_a_restart_returns_the_first_record_and_writes_nothing(odoo: Odoo) -> None:
    has_registry(odoo.script)
    odoo.script.expect(
        "res.partner", "search_read", [one_supplier_row(11, category_id=[], comment="<p>(tranibor-x)</p>")]
    )
    result = await _tools(odoo).create_supplier(
        name="Brightwater Office Supplies Ltd", country="GB", action_key="action-1"
    )
    assert result["supplier_id"] == "11" and result["created"] is True
    assert odoo.script.sent("res.partner", "create") == []
    [lookup] = odoo.script.sent("res.partner", "search_read")
    assert domain_of(lookup)[0][:2] == ["comment", "ilike"] and "action-1" not in json.dumps(lookup)


async def test_a_registration_number_unsupported_by_this_odoo_stops_the_create_through_the_tools(odoo: Odoo) -> None:
    no_registry(odoo.script)  # asked by the replay lookup, which finds nothing
    odoo.script.expect("res.partner", "search_read", [])
    with pytest.raises(NotSupported) as raised:
        await _tools(odoo).create_supplier(
            name="Quillfeather Stationery SAS", country="FR", registration_number="999 000 808", action_key="a-1"
        )
    assert str(raised.value).startswith("not_supported: registration_number is not supported by this backend")
    # the duplicate check needs the registration number, which this Odoo cannot search: nothing was created
    assert odoo.script.sent("res.partner", "create") == []


async def test_a_cancel_of_a_cancelled_request_and_of_an_ordered_one_write_nothing(odoo: Odoo) -> None:
    odoo.script.expect("purchase.order", "search_read", [{**ORDER_ROW, "state": "cancel"}])
    odoo.script.expect("purchase.order.line", "search_read", ORDER_LINES)
    result = await _tools(odoo).cancel_purchase_request("31", "again", action_key="k-1")
    assert result["cancelled"] is False
    odoo.script.expect("purchase.order", "search_read", [{**ORDER_ROW, "state": "purchase"}])
    with pytest.raises(Conflict):
        await _tools(odoo).cancel_purchase_request("31", "too late", action_key="k-2")
    assert odoo.script.sent("purchase.order", "button_cancel") == []


async def test_a_supplier_search_over_mcp_answers_not_supported_as_the_agent_reads_it(odoo: Odoo) -> None:
    from tranibor_purchasing.app import Runtime, build_server

    from .conftest import Mcp

    no_registry(odoo.script)
    runtime = Runtime(Settings.from_env(ODOO_ENV), odoo.backend)
    refused = await Mcp(build_server(runtime)).error("search_suppliers", registration_number="999 000 303")
    assert refused.startswith("not_supported: registration_number is not supported by this backend")
    assert KEY not in refused
