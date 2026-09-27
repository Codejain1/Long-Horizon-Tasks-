from textkit.core import title_case


def test_capitalises_words():
    assert title_case("hello world") == "Hello World"


def test_small_words_stay_lowercase_inside():
    assert title_case("the lord of the rings") == "The Lord of the Rings"
    assert title_case("war and peace") == "War and Peace"


def test_first_and_last_words_are_always_capitalised():
    assert title_case("a tale of two cities") == "A Tale of Two Cities"
    assert title_case("what are you looking at") == "What Are You Looking At"


def test_apostrophes():
    assert title_case("don't stop believing") == "Don't Stop Believing"


def test_empty():
    assert title_case("") == ""
