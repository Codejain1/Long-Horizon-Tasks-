import multiprocessing as mp

import notes


def _writer(store, worker):
    for i in range(30):
        notes.add_note(store, f"bg {worker}-{i}", ["bg"])


def test_edit_and_delete_while_others_write(tmp_path):
    store = str(tmp_path / "store")
    keep = notes.add_note(store, "draft", ["x"])
    gone = notes.add_note(store, "temporary", ["x"])
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(store, w)) for w in range(4)]
    for p in procs:
        p.start()
    notes.edit_note(store, keep, text="final", tags=["y"])
    notes.delete_note(store, gone)
    for p in procs:
        p.join(120)
    assert notes.get_note(store, keep)["text"] == "final" and list(notes.get_note(store, keep)["tags"]) == ["y"]
    ids = list(notes.list_notes(store))
    # The deleted note's content is gone (its id may be reused: the prompt doesn't forbid that), nothing else lost.
    texts = [notes.get_note(store, i)["text"] for i in ids]
    assert "temporary" not in texts and "final" in texts and len(ids) == 1 + 4 * 30
