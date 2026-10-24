import pytest
from conftest import T0, later, nurse, order, patient, stamp

import ward


def test_a_corrected_dose_no_longer_counts():
    o, n = order(max_daily=1_000_000), nurse()
    e = ward.administer(o, n)
    ward.correct_administration(e, "scanned the wrong patient")
    assert [d["corrected"] for d in ward.administrations(o)] == [True]
    assert ward.due_doses()[0]["due_at"] == stamp(T0)
    later(minutes=10)
    ward.administer(o, n)  # the limit has room again
    with pytest.raises(ward.WardError):
        ward.correct_administration(e, "again")


def test_corrections_never_change_the_record():
    """Rule from ticket 3: the audit trail is append-only; a correction is a new event pointing at the original."""
    p = patient()
    o = order(p)
    e = ward.administer(o, nurse())
    before = {x["id"]: dict(x) for x in ward.audit_log(p)}
    later(minutes=10)
    ward.correct_administration(e, "not given")
    after = ward.audit_log(p)
    assert all(before[x["id"]] == x for x in after if x["id"] in before) and set(before) <= {x["id"] for x in after}
    new = [x for x in after if x["id"] not in before]
    assert len(new) == 1 and e in new[0].values()
