from textkit.core import sentence_case


def test_capitalises_first_letter():
    assert sentence_case("hello world") == "Hello world"


def test_leaves_the_rest_alone():
    assert sentence_case("mcDonald's farm") == "McDonald's farm"


def test_empty():
    assert sentence_case("") == ""
