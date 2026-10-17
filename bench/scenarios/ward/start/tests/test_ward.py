import pytest

import ward


def test_patients_and_drugs():
    p = ward.add_patient("Asha", "MRN0012345", 62_000, ["Penicillin"])
    assert ward.get_patient(p)["allergies"] == ["penicillin"]
    ward.add_drug("PARA", "Paracetamol", "analgesic", 4_000_000)
    assert ward.get_drug("PARA")["max_daily_mcg"] == 4_000_000
    with pytest.raises(ward.WardError):
        ward.add_patient("X", "MRN1", 0)
