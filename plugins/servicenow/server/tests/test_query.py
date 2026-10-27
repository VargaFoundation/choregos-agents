# SPDX-License-Identifier: Apache-2.0
"""The encoded query: a caller's value never changes the query's structure."""

from __future__ import annotations

import pytest

from choregos_servicenow.errors import Refused
from choregos_servicenow.fake import parse_encoded
from choregos_servicenow.query import Filter, Op, Query, escape_value, like
from choregos_servicenow.tables import check_fields, check_table


def test_typed_filters_are_encoded() -> None:
    query = (
        Query("incident")
        .where(Filter(field="state", op=Op.in_, value=["1", "2"]), Filter(field="active", value="true"))
        .any_of(like("short_description", "vpn"), like("description", "vpn"))
        .order("sys_created_on", descending=True)
    )
    assert query.encode() == (
        "stateIN1,2^active=true^short_descriptionLIKEvpn^ORdescriptionLIKEvpn^ORDERBYDESCsys_created_on"
    )


def test_an_injected_or_clause_stays_inside_the_value() -> None:
    query = Query("incident").where(Filter(field="active", value="true"), like("short_description", "x^ORactive=false"))
    encoded = query.encode()
    assert encoded == "active=true^short_descriptionLIKEx^^ORactive=false"
    groups, order = parse_encoded(encoded)
    # Two AND conditions, no OR group, and the whole injection is the LIKE value.
    assert groups == [[("active", "=", "true")], [("short_description", "LIKE", "x^ORactive=false")]]
    assert order == []


def test_an_injected_orderby_or_new_query_stays_inside_the_value() -> None:
    for value in ("a^ORDERBYnumber", "a^NQactive=false", "^", "a^^b"):
        groups, order = parse_encoded(Query("incident").where(like("description", value)).encode())
        assert groups == [[("description", "LIKE", value)]]
        assert order == []


@pytest.mark.parametrize("value", ["javascript:gs.getUserID()", "x JavaScript:alert(1)", "a\nb", "a\x00", "\r"])
def test_script_and_control_characters_are_refused(value: str) -> None:
    with pytest.raises(Refused):
        escape_value(value)


def test_an_in_element_cannot_carry_a_comma() -> None:
    with pytest.raises(Refused, match="comma"):
        Query("incident").where(Filter(field="state", op=Op.in_, value=["1,7"]))


@pytest.mark.parametrize("name", ["password", "state^ORactive", "STATE", "caller_id.vip.x", "a=b", ""])
def test_fields_outside_the_table_or_malformed_are_refused(name: str) -> None:
    with pytest.raises(Refused):
        Query("incident").where(Filter(field=name, value="1"))


def test_a_dot_walk_from_a_served_reference_is_accepted() -> None:
    assert Query("incident").where(Filter(field="caller_id.vip", value="true")).encode() == "caller_id.vip=true"


def test_unary_operators_take_no_value_and_others_need_one() -> None:
    assert Query("incident").where(Filter(field="assigned_to", op=Op.is_empty)).encode() == "assigned_toISEMPTY"
    with pytest.raises(Refused, match="needs a value"):
        Query("incident").where(Filter(field="state", op=Op.eq))


@pytest.mark.parametrize("table", ["sys_user", "sys_properties", "incident;drop", "INCIDENT"])
def test_a_table_outside_the_list_is_refused(table: str) -> None:
    with pytest.raises(Refused, match="not served"):
        check_table(table)


def test_only_served_fields_can_be_asked_for() -> None:
    assert check_fields("incident", ["number"]) == ("sys_id", "number")
    with pytest.raises(Refused):
        check_fields("incident", ["password"])
