import pytest
from conftest import T0, nurse, order, stamp

import ward


def test_administer_records_a_dose():
    o, n = order(), nurse()
    e = ward.administer(o, n)
    [dose] = ward.administrations(o)
    assert (dose["id"], dose["order"], dose["nurse"], dose["dose_mcg"], dose["at"]) == (e, o, n, 1_000_000, stamp(T0))


def test_unknown_order_or_nurse_is_refused():
    o = order()
    with pytest.raises(ward.WardError):
        ward.administer(o, "nobody")
    with pytest.raises(ward.WardError):
        ward.administer("nothing", nurse())
