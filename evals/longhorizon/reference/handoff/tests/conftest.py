import pytest

import lending


@pytest.fixture(autouse=True)
def clean():
    lending.reset()
    lending.set_today(None)
    yield
    lending.set_today(None)
