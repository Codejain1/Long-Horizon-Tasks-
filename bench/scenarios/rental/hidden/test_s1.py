from conftest import home, owner

import rentals


def test_search_filters_and_sorts():
    o = owner()
    a = home(title="B flat", rent=3000000, by=o)
    b = home(title="A flat", rent=3000000, by=o)
    c = home(title="Cheap room", rent=1200000, by=o)
    home(title="Shop", kind="commercial", rent=900000, by=o)
    home(title="Far", city="Pune", state="MH", rent=1000000, by=o)
    found = rentals.search_properties(city="bengaluru", kind="residential")
    assert [p["id"] for p in found] == [c, b, a]
    assert [p["id"] for p in rentals.search_properties(city="Bengaluru", max_rent_cents=1200000)][-1] == c
    assert found[0]["rent_cents"] == 1200000
