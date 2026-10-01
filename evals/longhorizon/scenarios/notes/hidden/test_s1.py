import notes


def test_add_get_list(tmp_path):
    store = tmp_path / "store"
    a = notes.add_note(store, "buy milk", ["home"])
    b = notes.add_note(store, "ship release", ["work", "urgent"])
    assert notes.get_note(store, a)["text"] == "buy milk"
    assert set(notes.get_note(store, b)["tags"]) == {"work", "urgent"}
    assert set(notes.list_notes(store)) == {a, b}
    assert list(notes.list_notes(store, tag="work")) == [b]
