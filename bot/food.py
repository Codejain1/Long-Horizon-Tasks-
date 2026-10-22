"""Food apps, driven by code in the desktop Chrome where the owner is logged in. The model only reads page text into
lists; everything it returns is checked against that text (restaurant, item and price must be on the page), so
it can't invent a restaurant or a price. Never places an order: the furthest it goes is the checkout page.

    orders()          the order history page -> [{restaurant, items, total, date}]
    search(dish)      the search page's Dishes tab -> [{restaurant, item, price, rating, eta}]
    add(offer)        click that dish's ADD on the search page, then read the checkout page -> {"total", "text"}
"""

from __future__ import annotations

import re
import urllib.parse

import brain
import cdp
import login

# Every app the comparison may use. An app runs only once its real search page has been checked ("verified"):
# guessing how outside sites look was the main source of bugs (PROGRESS 2026-10-22).
PLATFORMS = {
    "swiggy": {"search": "https://www.swiggy.com/search?query={q}", "verified": True},
    "zomato": {"search": "https://www.zomato.com/{city}/delivery?q={q}", "verified": False},
    "magicpin": {"search": "https://magicpin.in/{city}/search/?q={q}", "verified": False},
    "eatclub": {"search": "https://www.eatclub.in/search?q={q}", "verified": False},
}
MAPS = "https://www.google.com/maps/search/{q}"
REVIEWS_SCHEMA = {"type": "object", "properties": {
    "rating": {"type": "number"}, "reviews": {"type": "number"},
    "liked": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 3},
    "disliked": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 3}},
    "required": ["rating", "reviews", "liked", "disliked"]}
LINKS_JS = """((names) => names.map(n => {
  const a = [...document.querySelectorAll('a[href]')].find(a => (a.innerText || '').toLowerCase().includes(n.toLowerCase()));
  return a ? a.href : null;
}))(%s)"""
ORDERS = "https://www.swiggy.com/my-account/orders"
SEARCH = "https://www.swiggy.com/search?query={q}"
CHECKOUT = "https://www.swiggy.com/checkout"
NONVEG = re.compile(r"\b(chicken|mutton|lamb|egg|eggs|fish|prawns?|keema|beef|pork|crab|seafood|non[- ]?veg)\b", re.I)


class NotReady(Exception):  # (cdp.Blocked, a rail, is also reported to the owner as is)
    """Swiggy isn't usable as is (logged out, location missing, blocked): the message says what the owner should do."""


class LoggedOut(NotReady):
    """Swiggy wants a login: the assistant logs in (phone + OTP or Google) and carries on."""


ORDERS_SCHEMA = {"type": "object", "properties": {"orders": {"type": "array", "items": {"type": "object", "properties": {
    "restaurant": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}},
    "total": {"type": "number"}, "date": {"type": "string"}}, "required": ["restaurant", "items", "total", "date"]}}},
    "required": ["orders"]}
OFFERS_SCHEMA = {"type": "object", "properties": {"offers": {"type": "array", "items": {"type": "object", "properties": {
    "restaurant": {"type": "string"}, "item": {"type": "string"}, "price": {"type": "number"},
    "rating": {"type": "string"}, "eta": {"type": "string"}}, "required": ["restaurant", "item", "price"]}}},
    "required": ["offers"]}


def _on_page(value, page: str) -> bool:
    return bool(value) and str(value).strip().lower() in page.lower()


def _price_on_page(price, page: str) -> bool:
    try:
        n = float(str(price).replace(",", "").replace("₹", ""))
    except ValueError:
        return False
    numbers = {float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", page)}
    return n in numbers


def read_orders(page: str, chat=brain.chat) -> list[dict]:
    got = brain.extract([{"role": "system", "content": "This is the text of the owner's Swiggy order history page. "
                          "List every order on it. Copy names exactly as written."},
                         {"role": "user", "content": page[:14000]}], ORDERS_SCHEMA, chat, max_tokens=3000)
    out = []
    for o in got.get("orders", []):
        items = [i for i in (o.get("items") or []) if _on_page(re.sub(r"\s*x\s*\d+$", "", str(i)), page)]
        if _on_page(o.get("restaurant"), page) and items and _price_on_page(o.get("total"), page):
            out.append({"restaurant": o["restaurant"], "items": items, "total": o["total"], "date": o.get("date", "")})
    return out


def read_offers(page: str, dish: str, chat=brain.chat) -> list[dict]:
    got = brain.extract([{"role": "system", "content": f"This is the text of a Swiggy search page for '{dish}'. List "
                          "every dish card on it that matches what was searched. Copy names and prices exactly as "
                          "written."}, {"role": "user", "content": page[:14000]}], OFFERS_SCHEMA, chat, max_tokens=3000)
    return [o for o in got.get("offers", [])
            if _on_page(o.get("restaurant"), page) and _on_page(o.get("item"), page) and _price_on_page(o.get("price"), page)]


def pick(offers: list[dict], max_total=None, veg: bool = False, n: int = 3) -> list[dict]:
    """Code, not the model: drop non-veg for a veg owner and anything over budget, cheapest first."""
    ok = [o for o in offers if not (veg and NONVEG.search(str(o.get("item"))))
          and (max_total is None or float(str(o["price"]).replace(",", "")) <= float(max_total))]
    seen, out = set(), []
    for o in sorted(ok, key=lambda o: float(str(o["price"]).replace(",", ""))):
        key = (o.get("platform", ""), o["restaurant"].lower(), o["item"].lower())  # same dish on two apps: two options
        if key not in seen:
            seen.add(key)
            out.append(o)
    return out[:n]


class Swiggy:
    def __init__(self, tab=cdp.Tab, chat=brain.chat, captures=None):
        """captures: a folder where a page the bot couldn't read is saved, to test the fix on the real page."""
        self.tab, self.chat, self.captures = tab, chat, captures

    def _capture(self, kind: str, page: str) -> None:
        if self.captures:
            import time
            from pathlib import Path
            d = Path(self.captures)
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}.txt").write_text(page)

    def _check(self, page: str) -> str:
        low = page.lower()
        if "access denied" in low or "captcha" in low:
            raise NotReady("Swiggy blocked the request from the server.")
        if login.looks_logged_out(page):
            raise LoggedOut("Swiggy is logged out in the bot's Chrome.")
        return page

    def orders(self) -> list[dict]:
        t = self.tab()
        try:
            page = self._check(t.open(ORDERS))
            for _ in range(3):  # older orders hide behind a button
                if not t.click_text("show more orders"):
                    break
                page = t.settle()
            got = read_orders(page, self.chat)
            if not got:
                self._capture("orders", page)
            return got
        finally:
            t.close()

    def _search_page(self, t, dish: str) -> str:
        page = self._check(t.open(SEARCH.format(q=urllib.parse.quote(dish))))
        if t.click_text("Dishes"):
            page = t.settle()
        if "location" in page.lower() and "₹" not in page:
            raise NotReady("Swiggy has no delivery address set in the bot's Chrome: set it there once.")
        return page

    def search(self, dish: str) -> list[dict]:
        t = self.tab()
        try:
            page = self._search_page(t, dish)
            got = read_offers(page, dish, self.chat)
            if not got:
                self._capture("search", page)
            links = (t.js(LINKS_JS % __import__("json").dumps([o["restaurant"] for o in got])) or []) if got else []
            for o, link in zip(got, links):
                o["platform"] = "swiggy"
                o["link"] = link or SEARCH.format(q=urllib.parse.quote(dish))
            return got
        finally:
            t.close()

    def add(self, offer: dict) -> dict:
        t = self.tab()
        try:
            self._search_page(t, offer["item"])
            if not t.click_text(offer["restaurant"], offer["item"], button="ADD"):
                raise NotReady(f"Couldn't find the ADD button for {offer['item']} from {offer['restaurant']} any more.")
            page = t.settle(timeout=8)
            if re.search(r"items already in cart|start afresh|replace cart", page, re.I):
                raise NotReady("Your Swiggy cart has items from another restaurant: clear it first, then ask again.")
            page = self._check(t.open(CHECKOUT))
            if offer["item"].lower() not in page.lower():
                self._capture("checkout", page)
            m = re.search(r"to pay\D{0,20}([\d,]+(?:\.\d+)?)", page, re.I)
            return {"total": m[1].replace(",", "") if m else None, "text": page[:3000],
                    "in_cart": offer["item"].lower() in page.lower()}
        finally:
            t.close()


def read_reviews(page: str, chat=brain.chat) -> dict | None:
    """Rating and what people say, from a Google Maps place page's text. None if the rating isn't on the page."""
    got = brain.extract([{"role": "system", "content": "This is the text of a restaurant's Google Maps page. Give its "
                          "star rating, number of reviews, and up to 3 things reviewers like and 3 they dislike, in "
                          "a few words each, taken from the reviews on the page."},
                         {"role": "user", "content": page[:12000]}], REVIEWS_SCHEMA, chat, max_tokens=400)
    r = got.get("rating")
    if not isinstance(r, (int, float)) or not 1 <= r <= 5 or f"{r:.1f}" not in page:
        return None
    return {"rating": r, "reviews": got.get("reviews"), "liked": got.get("liked", []), "disliked": got.get("disliked", [])}


def google_reviews(restaurant: str, area: str = "", tab=cdp.Tab, chat=brain.chat) -> dict | None:
    t = None
    try:
        t = tab()
        return read_reviews(t.open(MAPS.format(q=urllib.parse.quote(f"{restaurant} {area}".strip()))), chat)
    except Exception:
        return None  # reviews are a bonus: a failure never stops the comparison
    finally:
        if t:
            t.close()


def compare(dish: str, apps: dict, max_total=None, veg: bool = False, n: int = 3) -> tuple[list[dict], list[str]]:
    """The dish on every verified app, in parallel -> (best n by price within budget, apps that failed).
    apps: name -> object with .search(dish)."""
    from concurrent.futures import ThreadPoolExecutor
    found, failed = [], []
    with ThreadPoolExecutor(len(apps) or 1) as pool:
        futures = {name: pool.submit(app.search, dish) for name, app in apps.items()}
        for name, f in futures.items():
            try:
                for o in f.result(timeout=90):
                    link = o.get("link") or PLATFORMS[name]["search"].format(q=urllib.parse.quote(dish), city="bangalore")
                    found.append({**o, "platform": o.get("platform", name), "link": link})
            except NotReady:
                raise
            except Exception:
                failed.append(name)
    return pick(found, max_total, veg, n), failed


# ordering on the owner's behalf (rails v2: one-time permits checked against the page total; the owner approves the
# payment in their own UPI app) --------------------------------------------------------------------------------
CHECKOUT_SCHEMA = {"type": "object", "properties": {
    "items": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 10},
    "total": {"type": "number"}, "address": {"type": "string", "maxLength": 160}},
    "required": ["items", "total", "address"]}
STATUS_SCHEMA = {"type": "object", "properties": {
    "status": {"type": "string", "maxLength": 80}, "eta_minutes": {"type": "number"}, "delivered": {"type": "boolean"}},
    "required": ["status", "delivered"]}
PROCEED = ["Proceed to Pay", "Proceed to pay", "Pay Now"]
UPI_PAY = ["Verify and Pay", "Verify & Pay", "Pay Now", "Pay"]
PAID = re.compile(r"order (placed|confirmed|received)|payment (successful|received|done)|track (your )?order", re.I)
UNPAID = re.compile(r"payment (failed|declined|cancelled|unsuccessful)|request (expired|declined)|transaction failed", re.I)


def read_checkout(page: str, chat=brain.chat) -> dict | None:
    """Items, total and delivery address from the checkout page; None unless the total is on the page."""
    got = brain.extract([{"role": "system", "content": "This is the text of a food app's checkout page. Give the items "
                          "in the cart, the total to pay, and the delivery address, exactly as written."},
                         {"role": "user", "content": page[:10000]}], CHECKOUT_SCHEMA, chat, max_tokens=300)
    total = got.get("total")
    if not isinstance(total, (int, float)) or not _price_on_page(total, page) or not got.get("address"):
        return None
    return {"items": got.get("items", []), "total": float(total), "address": got["address"]}


def read_status(page: str, chat=brain.chat) -> dict:
    return brain.extract([{"role": "system", "content": "This is a food app's order page. For the most recent order give "
                           "its status in a few words (as the page says it), minutes to delivery if shown, and whether "
                           "it is delivered."}, {"role": "user", "content": page[:8000]}], STATUS_SCHEMA, chat,
                         max_tokens=120)


class Orders:
    """Swiggy checkout and tracking for an order the owner approved (item, total, address read from the page)."""

    def __init__(self, tab=cdp.Tab, chat=brain.chat, sleep=None, clock=None):
        import time as _t
        self.tab, self.chat, self.sleep, self.clock = tab, chat, sleep or _t.sleep, clock or _t.time

    def summary(self) -> dict | None:
        t = self.tab()
        try:
            return read_checkout(t.open(CHECKOUT), self.chat)
        finally:
            t.close()

    def pay(self, upi: str, total: float, wait_s: float = 360) -> str:
        """Checkout -> UPI -> the owner's UPI ID -> pay, each order click under a fresh permit for `total`.
        'paid', 'failed: ...' or 'timeout' (the owner didn't approve in time)."""
        t = self.tab()
        try:
            t.open(CHECKOUT)
            for label in PROCEED:  # into the payment step (an order click: permit, total checked on the page)
                t.allow_order(total)
                if t.click_label(label):
                    break
            t.settle(timeout=10)
            t.click_text("UPI")
            t.click_label("Add new UPI ID", "Add UPI ID", "Pay via UPI ID", "UPI ID")
            t.settle(timeout=6)
            if not t.type_field("upi_id", upi, {"upi": upi}):
                return "failed: couldn't find where to enter the UPI ID"
            for label in UPI_PAY:
                t.allow_order(total)
                if t.click_label(label):
                    break
            else:
                return "failed: couldn't find the pay button after the UPI ID"
            waited = 0.0
            while waited < wait_s:  # the owner approves in their UPI app
                self.sleep(10)
                waited += 10
                page = t.text()
                if PAID.search(page):
                    return "paid"
                if UNPAID.search(page):
                    return "failed: " + UNPAID.search(page)[0]
            return "timeout"
        finally:
            t.close()

    def status(self) -> dict:
        t = self.tab()
        try:
            return read_status(t.open(ORDERS), self.chat)
        finally:
            t.close()
