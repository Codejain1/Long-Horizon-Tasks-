from datetime import date

import pytest

import rentals

DAY = date(2026, 1, 1)
PAN, AADHAAR = "ABCDE1234F", "234567890123"


@pytest.fixture(autouse=True)
def clean():
    rentals.reset()
    rentals.set_today(DAY)
    yield
    rentals.set_today(None)


def owner(name="Meera", phone="9876543210"):
    return rentals.add_owner(name, phone)


def tenant(name="Arjun", phone="9123456789", verified=True):
    t = rentals.add_tenant(name, phone)
    if verified:
        rentals.verify_tenant(t, PAN, AADHAAR)
    return t


def home(state="KA", kind="residential", rent=2500000, title="2BHK, Indiranagar", city="Bengaluru", by=None):
    return rentals.add_property(by or owner(), title, city, state, kind, rent)


def accepted(prop=None, who=None, rent=None):
    prop = prop or home()
    offer = rentals.make_offer(prop, who or tenant(), rent or rentals.get_property(prop)["rent_cents"])
    rentals.accept_offer(offer)
    return offer


def agreement(prop=None, consent=True, months=11, start=DAY, who=None):
    return rentals.create_agreement(accepted(prop, who), start, months, consent_to_deploy=consent)


def funded(prop=None, consent=True, months=11, who=None):
    a = agreement(prop, consent, months, who=who)
    rentals.fund_deposit(a)
    return a
