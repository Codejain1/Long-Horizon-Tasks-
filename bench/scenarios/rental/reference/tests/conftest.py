import pytest

import rentals


@pytest.fixture(autouse=True)
def clean():
    rentals.reset()
    rentals.set_today(None)
    yield
    rentals.set_today(None)
