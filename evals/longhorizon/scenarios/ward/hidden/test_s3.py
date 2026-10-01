from conftest import later, nurse, order, patient

import ward


def test_egfr_readings():
    p = patient()
    assert ward.latest_egfr(p) is None
    ward.record_egfr(p, 55)
    later(hours=1)
    ward.record_egfr(p, 41)
    assert ward.latest_egfr(p) == 41


def test_audit_log_lists_everything_oldest_first():
    p = patient()
    o = order(p)
    later(minutes=5)
    ward.administer(o, nurse())
    later(minutes=5)
    ward.record_egfr(p, 60)
    log = ward.audit_log(p)
    assert len(log) == 3 and all({"id", "kind", "at"} <= set(e) for e in log)
    assert [e["at"] for e in log] == sorted(e["at"] for e in log)
