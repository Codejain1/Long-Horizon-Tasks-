import pytest
from conftest import drug, patient

import ward


def test_a_drug_of_the_allergy_class_is_refused():
    p = patient(allergies=["penicillin"])
    drug("AMOX", "penicillin", 3_000_000)
    with pytest.raises(ward.WardError):
        ward.prescribe(p, "AMOX", 500_000, 8, "Dr Rao")
    drug("PARA")
    ward.prescribe(p, "PARA", 500_000, 6, "Dr Rao")


def test_cross_reactive_classes_are_refused():
    """Rule from ticket 2: penicillins and cephalosporins cross-react both ways."""
    drug("AMOX", "penicillin", 3_000_000)
    drug("CEFX", "cephalosporin", 4_000_000)
    with pytest.raises(ward.WardError):
        ward.prescribe(patient(allergies=["penicillin"]), "CEFX", 500_000, 8, "Dr Rao")
    with pytest.raises(ward.WardError):
        ward.prescribe(patient(allergies=["cephalosporin"], mrn="MRN0099999"), "AMOX", 500_000, 8, "Dr Rao")
