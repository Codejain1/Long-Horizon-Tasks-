import multiprocessing as mp

from conftest import event

import boxoffice

COUNTERS, EACH, SEATS = 8, 20, 100


def _counter(path, event_id, out):
    refs = []
    try:
        boxoffice.open_box_office(path)
        for i in range(EACH):
            try:
                refs.append(boxoffice.book(event_id, f"guest {i}", 1))
            except boxoffice.BoxOfficeError:
                pass
        boxoffice.close_box_office()
    except Exception:  # a store that breaks under concurrent use fails the checks below, not the run
        pass
    finally:
        out.put(refs)


def _run(office):
    e = event(seats=SEATS, price="100.00")
    boxoffice.close_box_office()
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    procs = [ctx.Process(target=_counter, args=(office, e, out)) for _ in range(COUNTERS)]
    for p in procs:
        p.start()
    refs = []
    for _ in procs:
        try:
            refs += out.get(timeout=120)
        except Exception:  # a counter that died without reporting
            break
    for p in procs:
        p.join(10)
        if p.is_alive():
            p.terminate()
    boxoffice.reset()
    boxoffice.open_box_office(office)
    return e, refs


def test_concurrent_counters_never_double_sell(office):
    e, refs = _run(office)
    assert boxoffice.sold(e) <= SEATS and len(refs) <= SEATS and len(set(refs)) == len(refs)


def test_concurrent_counters_lose_no_booking(office):
    e, refs = _run(office)
    assert len(refs) == SEATS and boxoffice.sold(e) == SEATS
    assert all(boxoffice.get_booking(r)["status"] == "booked" for r in refs)
