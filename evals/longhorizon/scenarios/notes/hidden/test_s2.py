import notes


def test_search_all_words_case_insensitive(tmp_path):
    store = tmp_path / "store"
    a = notes.add_note(store, "Deploy the API on Friday", ["work"])
    notes.add_note(store, "Friday dinner", ["home"])
    assert list(notes.search(store, "friday api")) == [a]
    assert len(list(notes.search(store, "FRIDAY"))) == 2
