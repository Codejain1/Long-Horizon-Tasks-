import pytest
from conftest import drug, nurse, order, patient

import ward


def test_the_witness_is_recorded():
    p = patient()
    drug("MORPH", "opioid", 60_000)
    o = ward.prescribe(p, "MORPH", 10_000, 4, "Dr Rao")
    giver, witness = nurse("Ravi"), nurse("Lena")
    ward.administer(o, giver, witness=witness)
    assert ward.administrations(o)[0]["witness"] == witness
    plain = order(p, code="IBU")
    ward.administer(plain, giver)
    assert ward.administrations(plain)[0]["witness"] is None


def test_controlled_drugs_need_another_nurse_as_witness():
    """Rule from ticket 2: opioids need a second, registered nurse as witness."""
    drug("MORPH", "opioid", 60_000)
    o = ward.prescribe(patient(), "MORPH", 10_000, 4, "Dr Rao")
    giver = nurse("Ravi")
    for witness in (None, giver, "not-a-nurse"):
        with pytest.raises(ward.WardError):
            ward.administer(o, giver, witness=witness)
    assert ward.administrations(o) == []
