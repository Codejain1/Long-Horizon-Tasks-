from conftest import order, patient

import ward


def test_patient_drugs():
    p = patient()
    order(p, code="PARA")
    order(p, code="IBU")
    stopped = order(p, code="AMOX")
    order(p, code="PARA")
    ward.stop_order(stopped)
    assert ward.patient_drugs(p) == ["IBU", "PARA"]
