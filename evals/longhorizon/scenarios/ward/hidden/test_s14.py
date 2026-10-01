import pytest
from conftest import later, nurse, order, patient

import ward


def test_update_weight():
    p = patient()
    ward.update_weight(p, 68_500)
    assert ward.get_patient(p)["weight_g"] == 68_500
    assert ward.audit_log(p)[-1]["kind"] == "weight"
    with pytest.raises(ward.WardError):
        ward.update_weight(p, 0)


def test_weight_changes_rescale_the_limit():
    """Rule from ticket 1, with ticket 14's latest weight: 4 g scaled to 3.2 g at 40 kg."""
    p = patient(weight_g=60_000)
    o, n = order(p, dose=1_000_000), nurse()
    ward.update_weight(p, 40_000)
    for _ in range(3):
        ward.administer(o, n)
        later(hours=6)
    with pytest.raises(ward.WardError):
        ward.administer(o, n)
