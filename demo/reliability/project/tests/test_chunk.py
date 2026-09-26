import pytest

from textkit.core import chunk


def test_even_and_uneven():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
    assert chunk([1, 2, 3], 2) == [[1, 2], [3]]


def test_empty():
    assert chunk([], 3) == []


@pytest.mark.parametrize("size", [0, -1])
def test_rejects_non_positive_size(size):
    with pytest.raises(ValueError):
        chunk([1, 2], size)


def test_rejects_non_int_size():
    with pytest.raises(TypeError):
        chunk([1, 2], 1.5)
