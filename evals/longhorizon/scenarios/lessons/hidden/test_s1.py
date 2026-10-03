import pytest

import notes


def test_notes_survive_reopening(tmp_path):
    path = str(tmp_path / "notes")
    notes.open_notes(path)
    a, b = notes.add_note("buy milk"), notes.add_note("call Ravi")
    notes.close_notes()
    notes.open_notes(path)
    assert notes.get_note(a) == "buy milk" and sorted(notes.list_notes()) == sorted([a, b])
    with pytest.raises(notes.NotesError):
        notes.get_note("nope")
    notes.close_notes()
