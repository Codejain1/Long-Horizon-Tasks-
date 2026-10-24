import pytest
from conftest import T0, drug, order, patient, stamp

import ward


def test_prescribe_and_get_order():
    o = order()
    got = ward.get_order(o)
    assert (got["id"], got["drug"], got["dose_mcg"], got["every_hours"], got["status"]) == (o, "PARA", 1_000_000, 6,
                                                                                            "active")
    assert got["prescriber"] == "Dr Rao" and got["created_at"] == stamp(T0)


def test_bad_orders_are_refused():
    p, _ = patient(), drug()
    for dose, every in ((0, 6), (-5, 6), (2.5, 6), (100, 0), (100, 25)):
        with pytest.raises(ward.WardError):
            ward.prescribe(p, "PARA", dose, every, "Dr Rao")
    with pytest.raises(ward.WardError):
        ward.prescribe(p, "NOPE", 100, 6, "Dr Rao")


def test_orders_for_lists_oldest_first():
    p = patient()
    first, second = order(p), order(p, code="IBU")
    assert [o["id"] for o in ward.orders_for(p)] == [first, second]
