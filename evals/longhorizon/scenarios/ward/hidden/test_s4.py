import pytest
from conftest import T0, later, nurse, order, stamp

import ward


def test_due_doses():
    first = order(every=6)
    later(hours=1)
    second = order(code="IBU", every=8)
    ward.administer(second, nurse())
    assert [(d["order"], d["due_at"]) for d in ward.due_doses()] == [
        (first, stamp(T0)), (second, stamp(ward.now().replace(hour=17)))]


def test_stopped_orders_are_not_due_or_given():
    o = order()
    ward.stop_order(o)
    assert ward.get_order(o)["status"] == "stopped" and ward.due_doses() == []
    with pytest.raises(ward.WardError):
        ward.administer(o, nurse())
