import os
import time

import pytest

os.environ["TZ"] = "Asia/Kolkata"  # like the shop's CI and server
time.tzset()

import shop  # noqa: E402


@pytest.fixture(autouse=True)
def clean():
    shop.reset()
    shop.reset_orders()


def order(items, prices, placed_at=None):
    for name, qty in items.items():
        shop.add_item(name, qty)
    o = shop.place_order(items, prices)
    if placed_at is not None:
        shop.get_order(o["id"])["placed_at"] = placed_at
    return o
