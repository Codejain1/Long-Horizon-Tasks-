import pytest

import ward


@pytest.fixture(autouse=True)
def clean():
    ward.reset()
    yield
    ward.set_now(None)
