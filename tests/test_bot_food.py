"""Swiggy playbook: the model's lists are checked against the page; code picks; the cart flow stops at checkout."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
import food as F  # noqa: E402

ORDERS_PAGE = """Past Orders
Meghana Foods  Koramangala  ORDER #1234  Delivered on Sat, Oct 12
Paneer Biryani x 1, Coke x 1   Total Paid: ₹ 420
MTR  Lalbagh  ORDER #1240  Delivered on Fri, Oct 18
Masala Dosa x 2   Total Paid: ₹ 180
SHOW MORE ORDERS"""

SEARCH_PAGE = """Restaurants Dishes
By Meghana Foods 4.4 30-35 MINS
Paneer Biryani ₹320 ADD
By Biryani Blues 4.1 25 MINS
Chicken Biryani ₹289 ADD
By Paradise 4.3 40 MINS
Veg Biryani ₹249 ADD
Paneer Dum Biryani ₹410 ADD"""


def model(payload):
    return lambda messages, **kw: {"content": "Here: " + json.dumps(payload)}


def test_orders_are_checked_against_the_page():
    got = F.read_orders(ORDERS_PAGE, model({"orders": [
        {"restaurant": "Meghana Foods", "items": ["Paneer Biryani x 1", "Coke"], "total": 420, "date": "Oct 12"},
        {"restaurant": "MTR", "items": ["Masala Dosa"], "total": "180", "date": "Oct 18"},
        {"restaurant": "Truffles", "items": ["Burger"], "total": 350, "date": "Oct 20"},          # not on the page
        {"restaurant": "MTR", "items": ["Masala Dosa"], "total": 999, "date": "Oct 18"}]}))       # wrong total
    assert [(o["restaurant"], o["total"]) for o in got] == [("Meghana Foods", 420), ("MTR", "180")]


def test_offers_checked_then_picked_by_code():
    offers = F.read_offers(SEARCH_PAGE, "biryani", model({"offers": [
        {"restaurant": "Meghana Foods", "item": "Paneer Biryani", "price": 320, "rating": "4.4", "eta": "30-35 MINS"},
        {"restaurant": "Biryani Blues", "item": "Chicken Biryani", "price": 289},
        {"restaurant": "Paradise", "item": "Veg Biryani", "price": 249},
        {"restaurant": "Paradise", "item": "Paneer Dum Biryani", "price": 410},
        {"restaurant": "Paradise", "item": "Veg Biryani", "price": 199},              # invented price
        {"restaurant": "Behrouz", "item": "Veg Biryani", "price": 249}]}))            # invented restaurant
    assert len(offers) == 4
    top = F.pick(offers, max_total=400, veg=True)
    assert [(o["restaurant"], o["price"]) for o in top] == [("Paradise", 249), ("Meghana Foods", 320)]
    assert [o["item"] for o in F.pick(offers)][:2] == ["Veg Biryani", "Chicken Biryani"]


class FakeTab:
    pages = {}

    def __init__(self):
        self.clicks, self.url, self.closed = [], None, False

    def open(self, url):
        self.url = url
        return self.pages.get(url.split("?")[0], "")

    def settle(self, timeout=20):
        return self.pages.get("after:" + (self.clicks[-1][0] if self.clicks else ""), self.pages.get(self.url.split("?")[0], ""))

    def click_text(self, *needles, button=None):
        self.clicks.append((needles[0], needles, button))
        return needles[0] in ("Dishes", "Paradise")

    def close(self):
        self.closed = True


def test_cart_flow_stops_at_checkout_and_closes_its_tab():
    FakeTab.pages = {F.SEARCH.split("?")[0]: SEARCH_PAGE, "after:Dishes": SEARCH_PAGE, "after:Paradise": "added",
                     F.CHECKOUT: "Veg Biryani x 1  Item Total ₹249  Delivery ₹35  TO PAY ₹ 284  Proceed to pay"}
    tabs = []

    def factory():
        tabs.append(FakeTab())
        return tabs[-1]
    r = F.Swiggy(tab=factory).add({"restaurant": "Paradise", "item": "Veg Biryani", "price": 249})
    assert r["total"] == "284" and r["in_cart"]
    assert tabs[0].clicks[-1] == ("Paradise", ("Paradise", "Veg Biryani"), "ADD") and tabs[0].closed
    assert not any("pay" in str(c).lower() for c in tabs[0].clicks)  # never presses pay / place order


def test_logged_out_and_mixed_cart_are_explained():
    FakeTab.pages = {F.ORDERS: "Login  Enter your phone number  Continue"}
    try:
        F.Swiggy(tab=FakeTab).orders()
        raise AssertionError("should have raised")
    except F.NotReady as e:
        assert "logged out" in str(e)
    FakeTab.pages = {F.SEARCH.split("?")[0]: SEARCH_PAGE, "after:Paradise": "Items already in cart. Start afresh?"}
    try:
        F.Swiggy(tab=FakeTab).add({"restaurant": "Paradise", "item": "Veg Biryani", "price": 249})
        raise AssertionError("should have raised")
    except F.NotReady as e:
        assert "another restaurant" in str(e)
