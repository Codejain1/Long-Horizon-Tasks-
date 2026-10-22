"""Drive the desktop Chrome (where the owner is logged in) over the DevTools protocol, stdlib only.

Chrome listens on 127.0.0.1:9222 (hermes-chrome.service). The bot works in its own tab, so it never touches the
tabs the owner has open. `Tab.open(url)` waits until the page's text stops changing; `text()` is what a person
sees; `click_text(...)` clicks the smallest element whose text contains all the given strings.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import time
import urllib.parse
import urllib.request

DEVTOOLS = os.environ.get("CDP_URL", "http://127.0.0.1:9222")

# per-step timing: the assistant sets this to record each browser action (ms) in the turn's trace
import contextvars  # noqa: E402
TIMER: contextvars.ContextVar = contextvars.ContextVar("cdp_timer", default=None)


def _timed(fn):
    def wrap(self, *a, **kw):
        t0 = time.time()
        try:
            return fn(self, *a, **kw)
        finally:
            rec = TIMER.get()
            if rec:
                arg = str(a[0])[:60] if a else ""
                rec({"step": "browser", "action": fn.__name__, "arg": arg, "ms": int(1000 * (time.time() - t0))})
    return wrap

# The owner's rails (Hermes enforced them in its rails plugin; here every browser action passes this layer):
# - never open a payment gateway by URL;
# - never click anything that pays, orders or books, except once with a permit (`allow_order`) that the order step
#   creates after the owner chose "order for me", and only if the total on the page is within it;
# - typing (`type_field`) only: the owner's phone or a 4-8 digit login OTP on a sign-in page; a search (words, no
#   long digit runs) in a search box; the owner's exact UPI ID in a UPI/VPA field. Never into a card, CVV, expiry or
#   UPI PIN field. (Owner's rule changes 2026-10-22: login OTPs yes; orders paid by a UPI request the owner approves
#   in their own UPI app. Payment OTPs, cards and PINs: never.)
PAY_GATEWAYS = ("razorpay", "paytm", "payu", "juspay", "stripe.com", "billdesk", "ccavenue", "cashfree", "phonepe",
                "paypal", "pay.google", "checkout.com", "easebuzz", "instamojo")
COMMIT = (r"place (your )?order|pay now|pay\s*(₹|rs|inr)|make payment|proceed to pay|continue to pay|"
          r"confirm (and |& )?pay|confirm (order|booking)|complete (booking|purchase|order)|book now|reserve now|"
          r"buy now|submit order|order now|slide to pay|verify (and |& )?pay|^\s*pay\s*$")


PAY_FIELD = r"card|cvv|cvc|expiry|upi ?pin|mpin|net ?banking|ifsc"
LOGIN_PAGE = re.compile(r"\botp\b|one.?time|verification code|log ?in|sign ?in|mobile number|phone number|"
                        r"enter (your )?(mobile|phone)", re.I)


TOTAL = re.compile(r"(to pay|grand total|total payable|amount payable|bill total|total|amount|\bpay)\D{0,20}?"
                   r"([\d,]+(?:\.\d+)?)", re.I)  # "Pay ₹284" on payment steps too


class Blocked(Exception):
    """A rail stopped a browser action."""


def targets(devtools: str = DEVTOOLS) -> list[dict]:
    with urllib.request.urlopen(devtools + "/json/list", timeout=10) as r:
        return [t for t in json.loads(r.read()) if t.get("type") == "page"]


class WebSocket:
    """Just enough RFC 6455 for CDP: text frames out (masked), text frames in (fragments joined), ping answered."""

    def __init__(self, url: str, timeout: float = 30):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("websocket handshake: connection closed")
            head += chunk
        status = head.split(b"\r\n")[0]
        if b" 101 " not in status:
            raise ConnectionError(f"websocket handshake refused: {status!r}")
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                         if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, text: str) -> None:
        self._frame(0x1, text.encode())

    def recv(self) -> str:
        parts = []
        while True:
            b1, b2 = self._read(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x9:
                self._frame(0xA, data)
                continue
            if op == 0x8:
                raise ConnectionError("websocket closed by Chrome")
            parts.append(data)
            if b1 & 0x80:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Tab:
    def __init__(self, devtools: str = DEVTOOLS, ws_factory=WebSocket, target: dict | None = None):
        """A new tab of the bot's own, or (target) an existing one, e.g. Google's sign-in popup."""
        if target is None:
            req = urllib.request.Request(devtools + "/json/new?about:blank", method="PUT")
            with urllib.request.urlopen(req, timeout=10) as r:
                target = json.loads(r.read())
        self.id, self.devtools, self.n = target["id"], devtools, 0
        self.ws = ws_factory(target["webSocketDebuggerUrl"])

    def call(self, method: str, **params) -> dict:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"page script failed: {r['exceptionDetails'].get('text')}")
        return r.get("result", {}).get("value")

    @_timed
    def text(self) -> str:
        return self.js("document.body ? document.body.innerText : ''") or ""

    @_timed
    def settle(self, timeout: float = 20, sleep=time.sleep) -> str:
        """Wait until the page's text stops changing (single-page apps keep loading after 'complete')."""
        last, same, t = "", 0, 0.0
        while t < timeout:
            sleep(0.7)
            t += 0.7
            now = self.text()
            same = same + 1 if now == last and len(now) > 200 else 0
            if same >= 2:
                return now
            last = now
        return last

    @_timed
    def open(self, url: str) -> str:
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(g in host for g in PAY_GATEWAYS):
            raise Blocked(f"rails: the bot never opens payment pages ({host})")
        self.call("Page.navigate", url=url)
        return self.settle()

    def allow_order(self, max_total: float, minutes: float = 5) -> None:
        """One click that pays / orders becomes possible on this tab: once, within `minutes`, if the page's total is at
        most `max_total`. Only the order step calls this, after the owner chose "order for me"."""
        self.permit = {"max_total": float(max_total), "until": time.time() + 60 * minutes}

    def _commit(self, labels) -> bool:
        """True if this click pays/orders/books and a valid permit covers it (the permit is then used up); raises
        if it pays/orders/books without one; False for an ordinary click."""
        if not any(re.search(COMMIT, x, re.I) for x in labels if x):
            return False
        p = getattr(self, "permit", None)
        if not p or time.time() > p["until"]:
            raise Blocked(f"rails: the bot never clicks pay / order / book without the owner's go ({labels})")
        totals = [float(m[2].replace(",", "")) for m in TOTAL.finditer(self.text())]
        if not totals:
            raise Blocked("rails: no total visible on the page, so no order click")
        if max(totals) > p["max_total"]:
            raise Blocked(f"rails: total ₹{max(totals):g} is over what the owner approved (₹{p['max_total']:g})")
        self.permit = None
        return True

    @_timed
    def click_text(self, *needles: str, button: str | None = None) -> bool:
        """Click the smallest element whose text contains every needle (case-insensitive); with `button`, click the
        button/link with that exact text inside it instead. True if something was clicked. Rails: never a click on
        an element whose own text pays, orders or books (checked in the page, on the element about to be clicked)."""
        permitted = self._commit((*needles, button or ""))
        js = """((needles, button, forbidden) => {
          const has = el => needles.every(n => (el.innerText || '').toLowerCase().includes(n.toLowerCase()));
          let best = null;
          for (const el of document.querySelectorAll('body *')) {
            if (el.offsetParent === null || !has(el)) continue;
            if (!best || (el.innerText || '').length < (best.innerText || '').length) best = el;
          }
          if (!best) return false;
          let target = best;
          if (button) {
            let scope = best;
            for (let i = 0; i < 6 && scope; i++, scope = scope.parentElement) {
              const b = [...scope.querySelectorAll('button, [role=button], a, div')].find(
                x => (x.innerText || '').trim().toLowerCase() === button.toLowerCase() && x.offsetParent !== null);
              if (b) { target = b; break; }
            }
            if (target === best) return false;
          }
          const own = (target.innerText || target.value || target.getAttribute('aria-label') || '').trim();
          if (new RegExp(forbidden, 'i').test(own)) return 'blocked:' + own.slice(0, 80);
          target.scrollIntoView({block: 'center'}); target.click(); return true;
        })(%s, %s, %s)""" % (json.dumps(list(needles)), json.dumps(button), json.dumps("(?!)" if permitted else COMMIT))
        r = self.js(js)
        if isinstance(r, str) and r.startswith("blocked:"):
            raise Blocked(f"rails: refused to click '{r[8:]}' (pays / orders / books)")
        return bool(r)

    @_timed
    def click_label(self, *labels: str) -> str | None:
        """Click the first visible button/link whose own text is exactly one of `labels` (case-insensitive), e.g.
        "Sign In", "Get OTP", "Verify OTP". Returns the label clicked, or None. Same rails as click_text."""
        if self._commit(labels) and len(labels) != 1:
            raise Blocked("rails: an order click names exactly one button")
        js = """((labels) => {
          const want = labels.map(l => l.toLowerCase());
          const els = [...document.querySelectorAll('button, a, [role=button], input[type=submit], div, span')]
            .filter(e => e.offsetParent !== null);
          for (const w of want) {
            const hit = els.find(e => (e.innerText || e.value || '').trim().toLowerCase() === w);
            if (hit) { hit.scrollIntoView({block: 'center'}); hit.click(); return w; }
          }
          return null;
        })(%s)""" % json.dumps(list(labels))
        return self.js(js)

    def type_login(self, kind: str, value: str, owner_phone: str) -> bool:
        return self.type_field(kind, value, {"phone": owner_phone})

    @_timed
    def type_field(self, kind: str, value: str, owner: dict) -> bool:
        """The only typing the bot can do (see the rails at the top). kind: "phone" (exactly owner["phone"]), "otp"
        (4-8 digits, sign-in page), "search" (words, a search box, never on payment pages), "upi_id" (exactly
        owner["upi"], a UPI/VPA field, the page must be about UPI). True if typed."""
        digits = re.sub(r"\D", "", value)
        url = self.js("location.href") or ""
        gateway = any(g in urllib.parse.urlparse(url).netloc.lower() for g in PAY_GATEWAYS)
        page = self.text()
        if kind == "phone":
            mine = re.sub(r"\D", "", owner.get("phone") or "")[-10:]
            if not (mine and digits[-10:] == mine):
                raise Blocked("rails: only the owner's own phone number can be typed")
            text = digits[-10:]
        elif kind == "otp":
            if not re.fullmatch(r"\d{4,8}", digits):
                raise Blocked("rails: a login OTP is 4-8 digits")
            text = digits
        elif kind == "search":
            if not re.fullmatch(r"[\w\s&'.,()-]{2,60}", value) or len(digits) > 4:  # "4111 1111 ..." too (test)
                raise Blocked("rails: a search is a few words, at most 4 digits")
            if gateway:
                raise Blocked("rails: no typing on payment pages")
            text = value.strip()
        elif kind == "upi_id":
            if not owner.get("upi") or value.strip().lower() != owner["upi"].lower():
                raise Blocked("rails: only the owner's own UPI ID can be typed")
            if not re.search(r"\bupi\b|\bvpa\b", page, re.I):
                raise Blocked("rails: the UPI ID goes only on a UPI payment step")
            text = value.strip()
        else:
            raise Blocked(f"rails: the bot types nothing but a login phone, OTP, a search or the UPI ID ({kind})")
        if kind in ("phone", "otp"):
            if gateway:
                raise Blocked("rails: no typing on payment pages")
            if not LOGIN_PAGE.search(page):
                raise Blocked("rails: typing only on a sign-in page")
        js = """((kind, forbidden) => {
          const vis = [...document.querySelectorAll('input')].filter(i => i.offsetParent !== null && !i.disabled
            && !['hidden', 'password', 'checkbox', 'radio', 'submit'].includes(i.type));
          const label = i => [i.name, i.id, i.placeholder, i.getAttribute('aria-label'), i.autocomplete,
            (i.labels && i.labels[0] && i.labels[0].innerText)].join(' ').toLowerCase();
          const bad = new RegExp(forbidden, 'i');
          let fields;
          if (kind === 'phone') {
            fields = vis.filter(i => i.type === 'tel' || /phone|mobile/.test(label(i))).slice(0, 1);
          } else if (kind === 'search') {
            fields = vis.filter(i => i.type === 'search' || /search|dish|restaurant|food/.test(label(i))).slice(0, 1);
          } else if (kind === 'upi_id') {
            fields = vis.filter(i => /upi|vpa/.test(label(i)) && !/pin/.test(label(i))).slice(0, 1);
          } else {
            const one = vis.filter(i => i.autocomplete === 'one-time-code' || /otp|code|verification/.test(label(i)));
            const boxes = vis.filter(i => i.maxLength === 1);
            fields = boxes.length >= 4 ? boxes : one.slice(0, 1);
          }
          if (!fields.length) return {error: 'no ' + kind + ' field on the page'};
          if (fields.some(i => bad.test(label(i)))) return {error: 'blocked: a payment field'};
          window.__botFields = fields;
          return {n: fields.length};
        })(%s, %s)""" % (json.dumps(kind), json.dumps(PAY_FIELD))
        r = self.js(js) or {}
        if str(r.get("error", "")).startswith("blocked"):
            raise Blocked("rails: refused to type into a payment field")
        if r.get("error"):
            return False
        chunks = list(text) if r["n"] > 1 else [text]  # OTP boxes: one digit each
        for i, chunk in enumerate(chunks[:r["n"]] if r["n"] > 1 else chunks):
            self.js(f"window.__botFields[{i}].focus(); window.__botFields[{i}].select && window.__botFields[{i}].select()")
            self.call("Input.insertText", text=chunk)
        return True

    def press_enter(self) -> None:
        for t in ("keyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=t, key="Enter", code="Enter", windowsVirtualKeyCode=13)

    def scroll_end(self) -> None:
        self.js("window.scrollTo(0, document.body.scrollHeight)")

    def close(self) -> None:
        self.ws.close()
        try:
            urllib.request.urlopen(f"{self.devtools}/json/close/{self.id}", timeout=5).read()
        except Exception:
            pass
