import multiprocessing as mp

import notes

WORKERS, EACH = 8, 40


def _writer(store, worker):
    for i in range(EACH):
        notes.add_note(store, f"note {worker}-{i}", [f"w{worker}"])


def test_concurrent_writers_lose_nothing(tmp_path):
    store = str(tmp_path / "store")
    notes.add_note(store, "seed", [])
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(store, w)) for w in range(WORKERS)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert all(p.exitcode == 0 for p in procs)
    ids = list(notes.list_notes(store))
    assert len(ids) == WORKERS * EACH + 1 and len(set(ids)) == len(ids)
    texts = {notes.get_note(store, i)["text"] for i in ids}
    assert all(f"note {w}-{i}" in texts for w in range(WORKERS) for i in range(EACH))
