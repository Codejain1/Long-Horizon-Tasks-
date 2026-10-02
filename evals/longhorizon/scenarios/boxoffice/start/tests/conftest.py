import pytest

import boxoffice


@pytest.fixture(autouse=True)
def clean():
    boxoffice.reset()
    yield
