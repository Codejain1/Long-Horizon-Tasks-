import pytest

import invoicing


def pytest_collection_modifyitems(items):
    # CI runs the suite backwards, so a test that leans on state another test left behind fails here.
    items.reverse()


@pytest.fixture(autouse=True)
def clean():
    invoicing.reset()
    invoicing.set_now(None)
    yield
    invoicing.set_now(None)
