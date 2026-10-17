import pytest
from conftest import drug, nurse, order, patient

import ward


def test_a_batch_is_all_or_nothing():
    p, n = patient(), nurse()
    a, b = order(p), order(p, code="IBU")
    ward.stop_order(b)
    with pytest.raises(ward.WardError):
        ward.administer_batch([{"order": a, "nurse": n}, {"order": b, "nurse": n}])
    assert ward.administrations(a) == []
    ids = ward.administer_batch([{"order": a, "nurse": n}])
    assert [d["id"] for d in ward.administrations(a)] == ids


def test_a_batch_counts_its_own_doses():
    o, n = order(dose=1_000_000, max_daily=2_000_000), nurse()
    with pytest.raises(ward.WardError):
        ward.administer_batch([{"order": o, "nurse": n}] * 3)
    assert ward.administrations(o) == []


def test_controlled_drugs_need_a_witness_in_batches():
    """Rule from ticket 2 again, through the scanner."""
    p, giver = patient(), nurse("Ravi")
    drug("MORPH", "opioid", 60_000)
    m, para = ward.prescribe(p, "MORPH", 10_000, 4, "Dr Rao"), order(p)
    with pytest.raises(ward.WardError):
        ward.administer_batch([{"order": para, "nurse": giver}, {"order": m, "nurse": giver}])
    assert ward.administrations(para) == []
