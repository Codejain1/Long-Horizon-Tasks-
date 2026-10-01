from datetime import date

import pytest

import lending

DAY = date(2026, 3, 2)


@pytest.fixture(autouse=True)
def clean():
    lending.reset()
    lending.set_today(DAY)
    yield
    lending.set_today(None)


def member(name="Asha", email="asha@example.com"):
    return lending.add_member(name, email)


def book(title="Malgudi Days", author="R. K. Narayan", replacement_cents=45000):
    return lending.add_book(title, author, replacement_cents)
