from textkit.core import slugify


def test_basic():
    assert slugify("Hello, World!") == "hello-world"


def test_collapses_separators():
    assert slugify("  many   spaces -- and___underscores ") == "many-spaces-and-underscores"


def test_strips_accents():
    assert slugify("Crème Brûlée") == "creme-brulee"


def test_empty():
    assert slugify("!!!") == ""
