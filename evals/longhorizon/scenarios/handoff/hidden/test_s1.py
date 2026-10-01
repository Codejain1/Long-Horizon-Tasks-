from conftest import book

import lending


def test_find_books_matches_title_or_author_ignoring_case_sorted_by_title():
    book("the Guide", "R. K. Narayan")
    book("Swami and Friends", "R. K. Narayan")
    book("Godan", "Premchand")
    assert [b["title"] for b in lending.find_books("narayan")] == ["Swami and Friends", "the Guide"]
    assert [b["title"] for b in lending.find_books("GOD")] == ["Godan"]
    assert lending.find_books("tolkien") == []
