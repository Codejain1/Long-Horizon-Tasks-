"""Logins done by the bot: phone + OTP (the owner sends only the OTP) and Google; rails on typing; OTP never stored."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "bot"))
import cdp  # noqa: E402
import login as L  # noqa: E402

LOGIN_PAGE = "Login  Enter your phone number  Continue"
OTP_PAGE = "Enter OTP sent to +91 98xxxxxx01  Verify OTP"


class FakeTab:
    """A site: logged out until the right OTP is typed."""
    state = {"logged_in": False, "otp": "482913"}

    def __init__(self, target=None):
        self.url, self.typed, self.clicked, self.closed, self.page = None, [], [], False, ""
        self.ws = type("WS", (), {"close": lambda s: None})()

    def open(self, url):
        self.url = url
        self.page = "Past Orders  Your orders ₹420  Logout" if self.state["logged_in"] else LOGIN_PAGE
        return self.page

    def settle(self, timeout=20):
        return self.page

    def click_label(self, *labels):
        self.clicked.append(labels[0])
        if labels[0] in ("Login", "Continue", "Get OTP"):
            self.page = OTP_PAGE
        return labels[0]

    def click_text(self, *needles, button=None):
        self.clicked.append(needles[0])
        self.state["logged_in"] = True
        return True

    def type_login(self, kind, value, phone):
        self.typed.append((kind, value))
        if kind == "otp" and value == self.state["otp"]:
            self.state["logged_in"] = True
        return True

    def close(self):
        self.closed = True


def test_phone_then_otp_logs_in_and_the_tab_waits_for_the_otp():
    FakeTab.state = {"logged_in": False, "otp": "482913"}
    tabs = []
    lg = L.Login(tab=lambda target=None: tabs.append(FakeTab()) or tabs[-1], sleep=lambda s: None)
    assert lg.start("c", "swiggy", "9800000001") == "otp_sent"
    assert lg.waiting_for("c") == "swiggy" and not tabs[-1].closed
    assert tabs[-1].typed == [("phone", "9800000001")]
    assert lg.finish("c", "482913", "9800000001") is True and tabs[1].closed
    assert lg.waiting_for("c") is None
    assert lg.start("c", "swiggy", "9800000001") == "logged_in"


def test_wrong_otp_and_expiry():
    FakeTab.state = {"logged_in": False, "otp": "482913"}
    t = [1000.0]
    lg = L.Login(tab=lambda target=None: FakeTab(), sleep=lambda s: None, clock=lambda: t[0])
    lg.start("c", "swiggy", "9800000001")
    assert lg.finish("c", "111111", "9800000001") is False
    lg.start("c", "swiggy", "9800000001")
    t[0] += L.HOLD_S + 1
    assert lg.waiting_for("c") is None  # the OTP tab gave up


def test_google_uses_the_popup_and_the_owners_account():
    FakeTab.state = {"logged_in": False, "otp": "x"}
    calls = {"n": 0}

    def targets():
        calls["n"] += 1
        return [{"id": "main", "url": "https://www.redbus.in/"}] + (
            [{"id": "pop", "url": "https://accounts.google.com/o/oauth2"}] if calls["n"] > 1 else [])
    made = []
    lg = L.Login(tab=lambda target=None: made.append(target) or FakeTab(), targets=targets, sleep=lambda s: None)
    assert lg.google("redbus", "me@gmail.com") is True
    assert made[1] == {"id": "pop", "url": "https://accounts.google.com/o/oauth2"}
    assert lg.google("swiggy", "me@gmail.com") is False  # Swiggy offers no Google sign-in


def tab_with(page, url="https://www.swiggy.com/login"):
    tab = cdp.Tab.__new__(cdp.Tab)
    sent = []
    tab.text = lambda: page
    tab.js = lambda expr: url if expr == "location.href" else ({"n": 1} if "__botFields" in expr and "kind" in expr else None)
    tab.call = lambda method, **p: sent.append((method, p)) or {}
    return tab, sent


def test_typing_rails():
    tab, sent = tab_with(LOGIN_PAGE)
    assert tab.type_login("phone", "+91 98000 00001", "9800000001") is True
    assert sent[-1] == ("Input.insertText", {"text": "9800000001"})
    with pytest.raises(cdp.Blocked):
        tab.type_login("phone", "9111111111", "9800000001")  # not the owner's number
    with pytest.raises(cdp.Blocked):
        tab.type_login("otp", "12", "9800000001")             # not an OTP
    with pytest.raises(cdp.Blocked):
        tab.type_login("card", "4111111111111111", "9800000001")
    pay, _ = tab_with("Enter OTP to confirm payment of ₹284", url="https://api.razorpay.com/pay")
    with pytest.raises(cdp.Blocked):
        pay.type_login("otp", "482913", "9800000001")         # payment OTPs: never
    cart, _ = tab_with("Your cart  To pay ₹284", url="https://www.swiggy.com/checkout")
    with pytest.raises(cdp.Blocked):
        cart.type_login("otp", "482913", "9800000001")        # not a sign-in page
    field, _ = tab_with(LOGIN_PAGE)
    field.js = lambda expr: "https://www.swiggy.com/login" if expr == "location.href" else {"error": "blocked: a payment field"}
    with pytest.raises(cdp.Blocked):
        field.type_login("otp", "482913", "9800000001")


sys.path.insert(0, str(ROOT / "tests"))


def test_conversation_login_otp_redacted_and_task_resumed(tmp_path):
    from test_bot_assistant import FakeSwiggy, Model, OFFERS, bot, call, say
    import food

    class OutThenIn(FakeSwiggy):
        logged = False

        def search(self, dish):
            if not OutThenIn.logged:
                raise food.LoggedOut("Swiggy is logged out in the bot's Chrome.")
            return self.offers

    class FakeLogin:
        def __init__(self):
            self.chat = None

        def waiting_for(self, chat):
            return "swiggy" if self.chat == chat else None

        def start(self, chat, site, phone):
            self.chat = chat
            return "otp_sent"

        def finish(self, chat, otp, phone):
            self.chat = None
            OutThenIn.logged = otp == "482913"
            return OutThenIn.logged

        def google(self, site, email):
            return False
    m = Model(call("task_start", kind="food", slots={"dish": "veg biryani", "max_total": 400}))
    b = bot(tmp_path, m)
    b.swiggy, b.login = OutThenIn(OFFERS), FakeLogin()
    assert "login phone" in say(b, "order veg biryani under 400")  # no phone yet
    assert say(b, "login phone +91 98000 00001").startswith("Saved: I'll use your number ending 0001")
    m.replies.append(call("task_start", kind="food", slots={"dish": "veg biryani", "max_total": 400}))
    r = say(b, "order veg biryani under 400")
    assert "sent an OTP to your number ending 0001" in r
    r = say(b, "482913")
    assert r.startswith("Logged in to Swiggy ✅") and "1. Veg Biryani — Paradise — ₹249 on Swiggy" in r  # carried on
    stored = json.dumps(b.db.execute("SELECT * FROM log").fetchall() + b.db.execute("SELECT * FROM traces").fetchall())
    assert "482913" not in stored and "[login OTP]" in stored  # the OTP is used, never stored


def decision(action, **kw):
    return {"content": json.dumps({"action": action, "kind": kw.pop("kind", "none"), "details": kw.pop("details", {}),
                                   "reply": kw.pop("reply", ""), **kw})}


def test_live_failures_of_2026_10_21_replayed(tmp_path):
    """The owner's real messages that went wrong: login asked in words, a stuck form, an invented 'I found'."""
    from test_bot_assistant import Model, bot, say
    started = []

    class FakeLogin:
        def waiting_for(self, chat):
            return None

        def start(self, chat, site, phone):
            started.append(site)
            return "logged_in"

        def google(self, site, email):
            started.append(("google", site))
            return True

    # 1. "do login on goibibo with that" -> the login steps (Google, since it's set up), not a flight booking
    m = Model(decision("login", site="Goibibo"))
    b = bot(tmp_path, m)
    b.login = FakeLogin()
    b.store.set_fact("owner:google", "me@gmail.com")
    assert say(b, "google sign in done, do login on goibibo with that").startswith("Logged in to Goibibo with Google ✅")
    assert started == [("google", "goibibo")]
    m.replies.append(decision("login", site="Paytm"))
    assert say(b, "login to paytm").startswith("I can log in to swiggy, ixigo, redbus, goibibo, makemytrip, zomato")

    # 2. a form the owner never wanted: "i am just asking to sign in" leaves it, and goes to the login
    m.replies += [decision("book", kind="flight", details={"from": "goibibo", "to": "login", "date": "today"}),
                  decision("login", site="goibibo")]
    r = say(b, "do login on goibibo")
    assert "goibibo" not in json.dumps(b.store.task(1)["slots"]).lower()  # a site is never a place
    r = say(b, "i am just asking to sign in on goibibo with the email i have signed in")
    assert r.startswith("Logged in to Goibibo") and b.store.task(1)["status"] == "cancelled"

    # 3. "what should I eat now?" -> 3 ideas from the pattern (code lists them; no invented "I found")
    m.replies.append(decision("suggest_food"))
    m.replies.append({"content": json.dumps({"ideas": [{"dish": "Paneer Wrap", "why": "light"}, {"dish": "Veggie Sub",
                                                        "why": "your usual"}, {"dish": "Dosa", "why": "quick"}]})})
    r = say(b, "What should I eat now?")
    assert r.startswith("1. Paneer Wrap — light") and "found" not in r.lower()
    assert "NEVER say you searched" in m.seen[-2][0][0]["content"]



def test_logged_in_needs_positive_signs():
    assert not L.looks_logged_in("Zomato  Discover the best food  Log in  Sign up")  # live: was taken as logged in
    assert L.looks_logged_in("Past Orders  ORDER #1234  Logout")
    assert not L.looks_logged_in("Login  Enter your phone number")
