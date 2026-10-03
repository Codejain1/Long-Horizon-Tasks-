from conftest import run_workers

import notes

WORKERS, EACH = 4, 30


def _worker(path, w, out):
    ids = []
    try:
        notes.open_notes(path)
        for i in range(EACH):
            ids.append(notes.add_note(f"note {w}-{i}"))
        notes.close_notes()
    except Exception:
        pass
    finally:
        out.put(ids)


def test_workers_lose_no_note(tmp_path):
    path = str(tmp_path / "notes")
    notes.open_notes(path)
    notes.close_notes()
    ids = [i for r in run_workers(_worker, [(path, w) for w in range(WORKERS)]) for i in r]
    notes.open_notes(path)
    assert len(ids) == WORKERS * EACH and sorted(notes.list_notes()) == sorted(ids)
    notes.close_notes()
