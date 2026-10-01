import pytest

import shop


@pytest.fixture(autouse=True)
def clean():
    shop.reset()
    shop.reset_orders()
