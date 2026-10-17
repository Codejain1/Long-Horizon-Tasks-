import csv

from conftest import T0, drug, nurse, order, patient, stamp

import ward


def test_the_export_lists_every_dose(tmp_path):
    p, n, w = patient(), nurse("Ravi"), nurse("Lena")
    drug("MORPH", "opioid", 60_000)
    para, m = order(p), ward.prescribe(p, "MORPH", 10_000, 4, "Dr Rao")
    e1, e2 = ward.administer(para, n), ward.administer(m, n, witness=w)
    path = tmp_path / "doses.csv"
    ward.export_csv(str(path))
    rows = list(csv.DictReader(path.open()))
    assert list(rows[0]) == ["event", "patient_mrn", "drug", "dose_mcg", "given_at", "nurse", "witness"]
    assert [(r["event"], r["drug"], r["dose_mcg"], r["given_at"], r["nurse"], r["witness"]) for r in rows] == [
        (e1, "PARA", "1000000", stamp(T0), n, ""), (e2, "MORPH", "10000", stamp(T0), n, w)]


def test_exports_mask_the_mrn(tmp_path):
    """Rule from ticket 2: only the MRN's last 3 characters leave WardSafe."""
    ward.administer(order(patient(mrn="MRN0012345")), nurse())
    path = tmp_path / "doses.csv"
    ward.export_csv(str(path))
    [row] = list(csv.DictReader(path.open()))
    assert row["patient_mrn"] == "*******345"
