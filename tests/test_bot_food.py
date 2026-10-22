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

    def net_start(self):
        pass

    def net_json(self, contains="", limit=20):
        return [{"url": "https://www.swiggy.com/dapi/search", "body": {"cards": []}}]


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


def test_list_followed_by_more_text_is_read_not_crashed():
    import brain
    live = '{"offers": [{"restaurant": "Paradise", "item": "Veg Biryani", "price": 249}]} {"note": "more"} and some text'
    assert brain.first_json(live) == {"offers": [{"restaurant": "Paradise", "item": "Veg Biryani", "price": 249}]}
    assert brain.first_json('Sure! {"price": 7,500} done') == {"price": 7500}
    assert brain.first_json("no json here {") == {}
    offers = F.read_offers(SEARCH_PAGE, "biryani", lambda m, **k: {"content": live})
    assert [o["item"] for o in offers] == ["Veg Biryani"]


def test_unreadable_pages_are_captured_for_replay(tmp_path):
    FakeTab.pages = {F.SEARCH.split("?")[0]: "Restaurants Dishes\nsomething new ₹99"}
    sw = F.Swiggy(tab=FakeTab, chat=model({"offers": []}), captures=tmp_path / "cap")
    assert sw.search("biryani") == []
    saved = sorted(p.name for p in (tmp_path / "cap").iterdir())
    assert any(n.endswith("-search.txt") for n in saved) and any(n.endswith("-search-feed.txt") for n in saved)


def test_google_reviews_need_the_rating_on_the_page():
    page = "Paradise Biryani  4.3 ★★★★☆ (2,140)  Dine-in · Delivery  'Great biryani, big portions' 'Delivery was slow'"
    good = model({"rating": 4.3, "reviews": 2140, "liked": ["great biryani"], "disliked": ["slow delivery"]})
    assert F.read_reviews(page, good)["rating"] == 4.3
    assert F.read_reviews(page, model({"rating": 4.8, "reviews": 9, "liked": [], "disliked": []})) is None  # invented
    assert F.google_reviews("X", tab=lambda: (_ for _ in ()).throw(OSError("no chrome"))) is None  # never a blocker


def test_compare_across_apps_keeps_each_app_and_reports_failures():
    class App:
        def __init__(self, offers=None, boom=False):
            self.offers, self.boom = offers or [], boom

        def search(self, dish):
            if self.boom:
                raise RuntimeError("page changed")
            return self.offers
    same = {"restaurant": "Paradise", "item": "Veg Biryani"}
    top, failed = F.compare("veg biryani", {"swiggy": App([{**same, "price": 249}]), "zomato": App([{**same, "price": 239}]),
                                            "magicpin": App(boom=True)})
    assert [(o["platform"], o["price"]) for o in top] == [("zomato", 239), ("swiggy", 249)]
    assert failed == ["magicpin"] and top[0]["link"].startswith("https://www.zomato.com/bangalore/")


class PayTab:
    """Checkout -> payment step -> UPI ID -> pay; then the page shows the owner's approval (or not)."""
    def __init__(self, outcome="Order placed! Order ID: 192837465012", url_after=None):
        self.steps, self.permits, self.outcome, self.reads = [], [], outcome, 0
        self.url_after = url_after

    def js(self, expr):
        return self.url_after if (self.reads >= 3 and self.url_after) else "https://www.swiggy.com/checkout"

    def open(self, url):
        self.steps.append(("open", url))
        return "Veg Biryani x 1  Deliver to: Home, 7th Block Koramangala  TO PAY ₹284"

    def settle(self, timeout=20):
        return ""

    def allow_order(self, total, minutes=5):
        self.permits.append(total)

    def click_label(self, *labels):
        self.steps.append(("click", labels[0]))
        return labels[0] if labels[0] in ("Proceed to Pay", "Add new UPI ID", "Verify and Pay") else None

    def click_text(self, *needles, button=None):
        self.steps.append(("click", needles[0]))
        return True

    def type_field(self, kind, value, owner):
        self.steps.append(("type", kind, value))
        return True

    def text(self):
        self.reads += 1
        return "Waiting for payment" if self.reads < 3 else self.outcome

    def close(self):
        self.steps.append(("close",))


def test_checkout_read_back_needs_the_total_on_the_page():
    page = "Veg Biryani x 1  Deliver to: Home, 7th Block Koramangala  Item total ₹249  TO PAY ₹284"
    got = F.read_checkout(page, model({"items": ["Veg Biryani x 1"], "total": 284, "address": "Home, 7th Block Koramangala"}))
    assert got == {"items": ["Veg Biryani x 1"], "total": 284.0, "address": "Home, 7th Block Koramangala"}
    assert F.read_checkout(page, model({"items": [], "total": 199, "address": "Home"})) is None  # not on the page


def test_pay_by_upi_request_with_a_permit_per_order_click():
    t = PayTab()
    r = F.Orders(tab=lambda: t, sleep=lambda s: None).pay("me@okaxis", 284)
    assert r == "paid"
    assert ("type", "upi_id", "me@okaxis") in t.steps and t.steps[-1] == ("close",)
    assert ("click", "Proceed to Pay") in t.steps and ("click", "Verify and Pay") in t.steps
    assert set(t.permits) == {284}  # every order click under a permit for exactly the approved total
    assert F.Orders(tab=lambda: PayTab("Payment failed"), sleep=lambda s: None).pay("me@okaxis", 284).startswith("failed")
    assert F.Orders(tab=lambda: PayTab("Waiting"), sleep=lambda s: None).pay("me@okaxis", 284, wait_s=30) == "timeout"
    # a "Track your order" link on the payment page is NOT a payment (code review): no confirmation, no order id
    fooled = PayTab("Header: Track your order  |  Pay ₹284 via UPI  Waiting for approval")
    assert F.Orders(tab=lambda: fooled, sleep=lambda s: None).pay("me@okaxis", 284, wait_s=30) == "timeout"
    moved = PayTab("Order placed! Arriving in 30 mins", url_after="https://www.swiggy.com/order-confirmation/abc")
    assert F.Orders(tab=lambda: moved, sleep=lambda s: None).pay("me@okaxis", 284) == "paid"



def test_offer_fields_must_sit_on_one_card():
    page = ("By Paradise 4.3 40 MINS\nVeg Biryani ₹249 ADD\n" + "filler " * 120 +
            "By Meghana Foods 4.4\nPaneer Biryani ₹320 ADD")
    good = {"restaurant": "Paradise", "item": "Veg Biryani", "price": 249}
    crossed = {"restaurant": "Paradise", "item": "Paneer Biryani", "price": 249}  # dish from one card, shop from another
    got = F.read_offers(page, "biryani", model({"offers": [good, crossed]}))
    assert got == [good]


def test_reviews_must_be_about_the_restaurant():
    page = "Truffles  4.5 ★★★★★ (9,800)  Burgers"
    m = model({"rating": 4.5, "reviews": 9800, "liked": ["burgers"], "disliked": []})
    assert F.read_reviews(page, m, "Paradise") is None and F.read_reviews(page, m, "Truffles")["rating"] == 4.5


def test_add_to_cart_uses_the_restaurant_link_before_searching():
    FakeTab.pages = {"https://www.swiggy.com/restaurants/paradise-123": "Veg Biryani ₹249 ADD",
                     "after:Veg Biryani": "added", F.CHECKOUT: "Veg Biryani x 1  TO PAY ₹ 284"}
    opened = []

    class LinkTab(FakeTab):
        def open(self, url):
            opened.append(url)
            return super().open(url)

        def click_text(self, *needles, button=None):
            self.clicks.append((needles[0], needles, button))
            return needles[0] == "Veg Biryani"
    r = F.Swiggy(tab=LinkTab).add({"restaurant": "Paradise", "item": "Veg Biryani", "price": 249,
                                   "link": "https://www.swiggy.com/restaurants/paradise-123"})
    assert opened == ["https://www.swiggy.com/restaurants/paradise-123", F.CHECKOUT] and r["total"] == "284"



FEED = {"statusCode": 0, "data": {"cards": [
    {"card": {"card": {"@type": "type.googleapis.com/swiggy.gandalf.widgets.v2.Navigation", "tab": []}}},
    {"groupedCard": {"cardGroupMap": {"DISH": {"cards": [
        {"card": {"card": {"@type": "x.SearchFilterSortWidget"}}},
        {"card": {"card": {"@type": "type.googleapis.com/swiggy.presentation.food.v2.DishGroup",
                           "restaurant": {"info": {"id": "123", "name": "Paradise", "areaName": "HSR", "avgRating": 4.3,
                                                   "slugs": {"restaurant": "paradise-hsr-bangalore", "city": "bangalore"}}},
                           "dishes": [{"info": {"name": "Veg Biryani", "price": 24900, "isVeg": 1, "inStock": 1, "imageId": "abc"}},
                                      {"info": {"name": "Chicken Biryani", "price": 28900, "isVeg": 0, "inStock": 1}},
                                      {"info": {"name": "Sold Out Biryani", "price": 19900, "isVeg": 1, "inStock": 0}}]}}}]}}}}]}}


def test_swiggy_data_feed_is_read_exactly_without_the_model():
    got = F.read_feed(FEED)
    assert [(o["item"], o["price"], o["veg"]) for o in got] == [("Veg Biryani", 249.0, True), ("Chicken Biryani", 289.0, False)]
    assert got[0]["link"] == "https://www.swiggy.com/restaurants/paradise-hsr-bangalore-123" and got[0]["rating"] == "4.3"
    assert got[0]["image"].endswith("/abc") and got[0]["area"] == "HSR"
    assert [o["item"] for o in F.pick(got, veg=True)] == ["Veg Biryani"]  # Swiggy's own veg flag
    assert F.read_feed({"data": {"cards": [{}, {"groupedCard": {"cardGroupMap": {"RESTAURANT": {"cards": []}}}}]}}) == []


def test_search_uses_the_feed_at_the_owners_location_and_says_when_nothing_is_there():
    import json as _json
    calls = []

    class FeedTab(FakeTab):
        body = FEED

        def js(self, expr):
            calls.append(expr)
            return _json.dumps(self.body)
    sw = F.Swiggy(tab=FeedTab, chat=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model")))
    sw.where = {"lat": 12.9116, "lng": 77.6474}
    got = sw.search("veg biryani")
    assert got[0]["item"] == "Veg Biryani" and "lat=12.9116&lng=77.6474&str=veg%20biryani" in calls[-1]
    FeedTab.body = {"statusCode": 0, "data": {"cards": [{}, {"groupedCard": {"cardGroupMap": {"RESTAURANT": {"cards": []}}}}]}}
    try:
        sw.search("veg biryani")
        raise AssertionError("should have raised")
    except F.NothingHere as e:
        assert "nothing for veg biryani" in str(e)
