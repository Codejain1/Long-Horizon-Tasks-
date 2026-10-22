"""Logging in for the owner, in the bot's Chrome, so they don't do it on every site again and again.

Two ways, both driven by code (the model plays no part):
- phone + OTP: open the site's sign-in, type the owner's stored phone (cdp.type_login checks it is theirs),
  press "Get OTP"; the tab stays open; when the owner sends the OTP on WhatsApp, type it, verify, check.
- Google: the owner signs in to Google once, by hand, in the bot's Chrome (Google blocks automated sign-in).
  Then "Continue with Google" and their account in Google's popup.
The OTP itself is never stored (not in memory, the chat log or traces): assistant.py redacts it before logging.
"""

from __future__ import annotations

import re
import time

import cdp

# per site: home page, a page that only works logged in, button labels (tried in order), Google offered?
SITES = {
    "swiggy": {"home": "https://www.swiggy.com/", "check": "https://www.swiggy.com/my-account/orders",
               "open": ["Sign In", "Sign in", "Login"], "send": ["Login", "Continue", "Get OTP", "Send OTP"],
               "verify": ["Verify OTP", "Verify", "Continue", "Login"], "google": False},
    "ixigo": {"home": "https://www.ixigo.com/", "check": "https://www.ixigo.com/profile",
              "open": ["Log in/Sign up", "Login/Signup", "Log in", "Login"], "send": ["Continue", "Get OTP", "Send OTP"],
              "verify": ["Verify", "Verify OTP", "Continue", "Login"], "google": True},
    "redbus": {"home": "https://www.redbus.in/", "check": "https://www.redbus.in/account",
               "open": ["Login / Sign Up", "Login/Signup", "Account", "Login"], "send": ["Get OTP", "Continue", "Send OTP"],
               "verify": ["Verify OTP", "Verify", "Login", "Continue"], "google": True},
    "goibibo": {"home": "https://www.goibibo.com/", "check": "https://www.goibibo.com/accounts/profile/",
                "open": ["Login / Signup", "Login/Signup", "Log in", "Login"], "send": ["Continue", "Get OTP"],
                "verify": ["Verify", "Verify OTP", "Continue", "Login"], "google": True},
    "makemytrip": {"home": "https://www.makemytrip.com/", "check": "https://www.makemytrip.com/my-trips/",
                   "open": ["Login or Create Account", "Login / Signup", "Login"], "send": ["Continue", "Get OTP"],
                   "verify": ["Verify & Login", "Verify", "Continue", "Login"], "google": True},
    "zomato": {"home": "https://www.zomato.com/", "check": "https://www.zomato.com/profile",
               "open": ["Log in", "Login"], "send": ["Send One Time Password", "Send OTP", "Continue"],
               "verify": ["Verify", "Verify OTP", "Continue"], "google": True},
}
GOOGLE = ["Continue with Google", "Sign in with Google", "Login with Google", "Google"]
HOLD_S = 600  # an OTP tab waits this long for the owner's OTP


LOGGED_IN = re.compile(r"\b(log ?out|sign ?out|my account|my profile|past orders|your orders|order #|my trips|"
                       r"my bookings)\b", re.I)


def looks_logged_in(page: str) -> bool:
    """Positive signs only (a page without a login form isn't proof: live 'already logged in to Zomato')."""
    return bool(LOGGED_IN.search(page)) and not looks_logged_out(page)


def looks_logged_out(page: str) -> bool:
    low = page.lower()
    return (("login" in low or "log in" in low or "sign in" in low) and ("phone number" in low or "mobile number" in low)
            and "₹" not in page) or ("sign up" in low and "₹" not in page and len(page) < 4000)


class Login:
    def __init__(self, tab=cdp.Tab, targets=cdp.targets, sleep=time.sleep, clock=time.time):
        self.tab, self.targets, self.sleep, self.clock = tab, targets, sleep, clock
        self.waiting: dict[str, tuple] = {}  # chat -> (tab, site, opened at)

    def logged_in(self, site: str) -> bool:
        t = self.tab()
        try:
            return looks_logged_in(t.open(SITES[site]["check"]))
        finally:
            t.close()

    def start(self, chat: str, site: str, phone: str) -> str:
        """'logged_in' (nothing to do), 'otp_sent' (waiting for the owner's OTP), or what went wrong."""
        if site not in SITES:
            return f"I don't know {site}'s sign-in yet."
        if self.logged_in(site):
            return "logged_in"
        s, t = SITES[site], self.tab()
        try:
            t.open(s["home"])
            t.click_label(*s["open"])
            t.settle(timeout=8)
            if not t.type_login("phone", phone, phone):
                t.close()
                return f"I opened {site}'s sign-in but couldn't find where to enter the phone number."
            t.click_label(*s["send"])
            page = t.settle(timeout=10)
            if not re.search(r"\botp\b|one.?time|verification code|enter (the )?code", page, re.I):
                t.close()
                return f"I entered your number on {site} but no OTP step appeared."
        except cdp.Blocked as e:
            t.close()
            return str(e)
        self._drop(chat)
        self.waiting[chat] = (t, site, self.clock())
        return "otp_sent"

    def waiting_for(self, chat: str) -> str | None:
        w = self.waiting.get(chat)
        if w and self.clock() - w[2] > HOLD_S:
            self._drop(chat)
            return None
        return w[1] if w else None

    def finish(self, chat: str, otp: str, phone: str) -> bool:
        t, site, _ = self.waiting.pop(chat)
        try:
            if not t.type_login("otp", otp, phone):
                return False
            t.click_label(*SITES[site]["verify"])  # many sites submit by themselves once the code is in
            t.settle(timeout=10)
        finally:
            t.close()
        return self.logged_in(site)

    def google(self, site: str, email: str) -> bool:
        """'Continue with Google' and the owner's account in Google's popup (Google signed in once by the owner)."""
        s = SITES.get(site)
        if not s or not s["google"]:
            return False
        t = self.tab()
        try:
            t.open(s["home"])
            t.click_label(*s["open"])
            t.settle(timeout=8)
            before = {x["id"] for x in self.targets()}
            if not t.click_label(*GOOGLE):
                return False
            for _ in range(10):
                self.sleep(1)
                pop = [x for x in self.targets() if x["id"] not in before and "accounts.google.com" in x.get("url", "")]
                if pop:
                    g = self.tab(target=pop[0])
                    try:
                        g.settle(timeout=8)
                        g.click_text(email)
                    finally:
                        g.ws.close()
                    break
            self.sleep(4)
        finally:
            t.close()
        return self.logged_in(site)

    def _drop(self, chat: str) -> None:
        w = self.waiting.pop(chat, None)
        if w:
            w[0].close()
