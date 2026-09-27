from textkit.core import word_count


def test_simple():
    assert word_count("one two three") == 3


def test_extra_whitespace_and_newlines():
    assert word_count("  one\ttwo\n\nthree  ") == 3


def test_punctuation_is_not_a_word():
    assert word_count("wait - what ?! yes") == 3


def test_contractions_and_hyphens_are_one_word():
    assert word_count("don't re-run it") == 3


def test_empty():
    assert word_count("") == 0
