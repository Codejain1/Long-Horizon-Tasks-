"""Hard rails for Hermes: money, orders, secrets and public links are decided in code, not by the model.

Hooks: pre_tool_call (block / ask the owner on WhatsApp), post_tool_call (audit log + remembers the page Hermes last
saw, so a click on "@e12" can be matched to its label), pre_llm_call (STOP / RESUME), and an always-on system prompt
section telling the model the rules (the code enforces them either way).

Owner's rules (2026-10-15): food is cash on delivery only and every order needs their YES; flights and hotels are
never paid by Hermes (it prepares, the owner pays); Hermes never types card numbers, CVV, OTP or UPI PIN; secrets
on this machine are never read; public URLs need the owner's YES.

UPI handoff (2026-10-21, opt-in): once the owner writes their own UPI ID into ~/.hermes/rails/upi_id (only the owner
can: it never comes from the model), on payment pages Hermes may choose UPI, type exactly that ID and press the pay
button after the owner's YES; the request then reaches the owner's UPI app, where they approve it with their PIN.
Cards, net banking, wallets and pay-later stay blocked; so do the PIN, OTP and any other text on payment pages.
Without the file, the old rails apply (travel never paid, food cash on delivery).
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

PAY_GATEWAYS = ("razorpay", "paytm", "payu", "juspay", "stripe.com", "billdesk", "ccavenue", "cashfree", "phonepe",
                "paypal", "pay.google", "checkout.com", "easebuzz", "instamojo")
FOOD = ("swiggy", "zomato", "magicpin", "eatsure", "dominos", "zepto", "blinkit", "pizzahut", "kfc", "mcdelivery",
        "instamart")
TRAVEL = ("makemytrip", "goibibo", "cleartrip", "ixigo", "easemytrip", "booking.com", "agoda", "airbnb", "expedia",
          "trip.com", "yatra", "goindigo", "airindia", "akasaair", "spicejet", "oyorooms", "hotels.com", "skyscanner",
          "redbus", "irctc")
INTERACT = {"browser_click", "browser_type", "browser_press", "browser_exec", "browser_cdp", "browser_dialog",
            "computer_use"}
SCRIPTED = {"browser_exec", "browser_cdp", "computer_use"}  # can click anything without a readable label

COMMIT = re.compile(r"place (your )?order|pay now|pay\s*(₹|rs|inr)|make payment|proceed to pay|continue to pay|"
                    r"confirm (and |& )?pay|confirm (order|booking)|complete (booking|purchase|order)|book now|"
                    r"reserve now|buy now|submit order|checkout|proceed to (checkout|payment)|order now|slide to pay",
                    re.I)
NON_COD = re.compile(r"\bupi\b|\bcards?\b|credit card|debit card|card number|net ?banking|\bwallet\b|paytm|phonepe|gpay|"
                     r"google pay|amazon pay|\bsimpl\b|lazypay|pay ?later|\bemi\b|saved card", re.I)
OTHER_PAY = re.compile(r"\bcards?\b|credit card|debit card|card number|net ?banking|\bwallet\b|paytm|phonepe|gpay|google pay|"
                       r"amazon pay|\bsimpl\b|lazypay|pay ?later|\bemi\b|saved card", re.I)
UPI_OPT = re.compile(r"\bupi\b|\bvpa\b", re.I)
PAY_BTN = re.compile(COMMIT.pattern + r"|verify (and |& )?pay|send (payment )?request|request (payment|money)|"
                     r"\bpay\s*(₹|rs|inr|now)", re.I)
UPI_ID = re.compile(r"^[\w.\-]{2,256}@[a-z][a-z0-9]{1,63}$", re.I)
PAY_FIELD = re.compile(r"\bcvv\b|\bcvc\b|\botp\b|one.time.password|upi ?pin|\bmpin\b|card ?number|expiry|"
                       r"name on card", re.I)
SECRETS = re.compile(r"\.hermes/\.env|\.hermes/auth\.json|whatsapp/session|creds\.json|\.ssh/|id_ed25519|id_rsa|"
                     r"\.aws/|hermes_model|secrets?\.(ya?ml|json)", re.I)
TUNNEL = re.compile(r"cloudflared|ngrok|localtunnel|\blt --port|serveo\.net|localhost\.run|tailscale funnel", re.I)

HOME = Path.home() / ".hermes" / "rails"
_state: dict[str, dict] = {}  # session -> {"url": last url, "page": last browser output}
_lock = threading.Lock()


def _luhn_card(text: str) -> bool:
    for m in re.finditer(r"(?:\d[ -]?){13,19}", text):
        digits = [int(c) for c in m[0] if c.isdigit()][::-1]  # Luhn: from the right, double every second digit
        if 13 <= len(digits) <= 19:
            if sum(d if i % 2 == 0 else (d * 2 - 9 if d > 4 else d * 2) for i, d in enumerate(digits)) % 10 == 0:
                return True
    return False


def _site(url: str, names) -> str | None:
    u = (url or "").lower()
    return next((n for n in names if n in u), None)


def _label(page: str, ref: str) -> str:
    """The snapshot line that carries this ref ('@e12' -> '- button "Place Order" [ref=e12]')."""
    r = re.escape(str(ref).lstrip("@"))
    m = re.search(rf"[^\n]*(\[ref={r}\]|\[@?{r}\]|@{r}\b)[^\n]*", page or "")
    return m[0].strip() if m else ""


def upi_id() -> str | None:
    """The owner's own UPI ID, set by the owner in HOME/upi_id; None = UPI handoff off."""
    try:
        v = (HOME / "upi_id").read_text().strip()
    except OSError:
        return None
    return v.lower() if UPI_ID.match(v) else None


def _upi_step(tool: str, typed: str, seen: str, upi: str, where: str) -> dict | None:
    """On a payment page with the handoff on: choose UPI, type the owner's UPI ID, the pay button asks the owner."""
    if tool in SCRIPTED:
        return {"action": "block", "message": f"Blocked by rails: on the payment page of {where} use clicks and typing "
                                              "on labelled fields only."}
    if typed:
        if typed.strip().lower() == upi:
            return None
        return {"action": "block", "message": "Blocked by rails: on payment pages Hermes types only the owner's UPI ID."}
    if PAY_BTN.search(seen):
        return {"action": "approve", "message": f"Send a UPI payment request from {where} to your UPI app ({upi})? Check "
                                                "the summary Hermes sent (what, when, total) first; you approve the "
                                                "payment with your PIN in your UPI app."}
    if UPI_OPT.search(seen) and not OTHER_PAY.search(seen):
        return None
    return {"action": "block", "message": f"Blocked by rails: on the payment page of {where} Hermes may only choose UPI, "
                                          "type the owner's UPI ID and press pay (with the owner's YES)."}


def halted() -> bool:
    return (HOME / "HALT").exists()


def decide(tool: str, args: dict[str, Any], state: dict) -> dict | None:
    """Return a Hermes pre_tool_call directive ({"action": "block"|"approve", "message"}) or None to allow."""
    if halted():
        return {"action": "block", "message": "STOPPED by the owner. Do nothing until they say RESUME."}
    text = json.dumps(args, ensure_ascii=False)
    url = str(args.get("url") or state.get("url") or "")
    label = _label(state.get("page", ""), args["ref"]) if args.get("ref") else ""
    seen = f"{text} {label}"

    if SECRETS.search(text):
        return {"action": "block", "message": "Blocked by rails: credentials and secrets on this machine are off limits."}

    typed = str(args.get("text") or args.get("keys") or args.get("value") or "")
    if tool in INTERACT and (_luhn_card(typed) or (typed and PAY_FIELD.search(label))):
        return {"action": "block", "message": "Blocked by rails: Hermes never enters card numbers, CVV, OTP or UPI PIN. "
                                              "Send the owner the payment link instead."}

    upi = upi_id()
    travel, food = _site(url, TRAVEL), _site(url, FOOD)
    pay_page = _site(url, PAY_GATEWAYS) or (travel and re.search(r"/(payment|pay|checkout)\b", url) and travel)
    if tool in INTERACT and upi and pay_page:
        return _upi_step(tool, typed, seen, upi, pay_page)
    if tool in INTERACT and upi and travel and PAY_BTN.search(seen):
        return {"action": "approve", "message": f"Hermes wants to go to payment on {travel} ({label or text[:80]}). "
                                                "Check the summary it sent (what, when, total) before you approve."}

    if tool in INTERACT and _site(url, PAY_GATEWAYS):
        return {"action": "block", "message": "Blocked by rails: Hermes never acts on payment pages. Send the owner the "
                                              "link and the amount; they pay themselves."}

    if tool in INTERACT and travel and (COMMIT.search(seen) or re.search(r"/(payment|pay|checkout)\b", url)):
        return {"action": "block", "message": f"Blocked by rails: Hermes does not book or pay on {travel}. Stop here and "
                                              "send the owner the trip summary (price, dates, times) and the page link."}

    if tool in INTERACT and food and upi and typed.strip().lower() == upi:
        return None
    if tool in INTERACT and food and NON_COD.search(seen) and (OTHER_PAY.search(seen) or not upi):
        return {"action": "block", "message": f"Blocked by rails: on {food} only Cash on Delivery"
                                              + (" or UPI" if upi else "") + " is allowed. Choose COD."}
    if tool in INTERACT and food and (COMMIT.search(seen) or tool in SCRIPTED):
        return {"action": "approve", "message": f"Place this order on {food}? Cash on delivery"
                                                + (f" or a UPI request to {upi}" if upi else "") + ". Check the summary "
                                                "Hermes sent (items, total) before you approve."}

    if tool in INTERACT and (travel or _site(url, PAY_GATEWAYS)) and tool in SCRIPTED:
        return {"action": "approve", "message": f"Hermes wants to run a scripted action on {travel or url}. Approve?"}
    if tool in INTERACT and COMMIT.search(seen):
        return {"action": "approve", "message": f"Hermes wants to press a buy/confirm button ({label or text[:80]}). Approve?"}

    if tool in ("terminal", "execute_code", "process") and TUNNEL.search(text):
        return {"action": "approve", "message": "Hermes wants to publish a public URL from this machine. Approve?"}
    return None


def _key(kw: dict) -> str:
    return str(kw.get("session_id") or kw.get("task_id") or "default")


def pre_tool_call(tool_name: str = "", args: dict | None = None, **kw):
    args = args if isinstance(args, dict) else {}
    with _lock:
        st = _state.setdefault(_key(kw), {})
        if tool_name == "browser_navigate" and args.get("url"):
            st["url"] = str(args["url"])
        directive = decide(tool_name, args, dict(st))
    if directive:
        _audit({"event": "rails", "tool": tool_name, "directive": directive["action"], "message": directive["message"],
                "url": st.get("url", ""), "args": json.dumps(args, ensure_ascii=False)[:300], "session": _key(kw)})
    return directive


def post_tool_call(tool_name: str = "", args: dict | None = None, result: Any = None, **kw):
    out = str(result or "")
    if tool_name.startswith("browser_"):
        with _lock:
            st = _state.setdefault(_key(kw), {})
            if "[ref=" in out or "@e" in out:
                st["page"] = out[-60000:]
            m = re.search(r"https?://[^\s\"'<>]+", out[:2000])
            if m and tool_name in ("browser_navigate", "browser_snapshot", "browser_click", "browser_back"):
                st["url"] = m[0]
    _audit({"event": "tool", "tool": tool_name, "args": json.dumps(args or {}, ensure_ascii=False)[:300],
            "result": out[:200], "session": _key(kw)})


def pre_llm_call(user_message: str = "", **kw):
    msg = (user_message or "").strip().upper().rstrip(".!")
    if msg == "STOP":
        HOME.mkdir(parents=True, exist_ok=True)
        (HOME / "HALT").write_text(time.ctime())
        _audit({"event": "halt", "session": _key(kw)})
        return {"context": "The owner said STOP: every action is now blocked. Reply only: Stopped. Say RESUME to continue."}
    if msg == "RESUME" and halted():
        (HOME / "HALT").unlink(missing_ok=True)
        _audit({"event": "resume", "session": _key(kw)})
        return {"context": "The owner said RESUME: actions are allowed again. Reply only: Resumed."}
    return None


UPI_RULES = """
- UPI handoff is on: on the payment page choose UPI, type the owner's UPI ID exactly, then press pay; the rails ask the
  owner first and the request goes to their UPI app, where they approve with their PIN. Never any other method."""

RULES = """## Hard rails (enforced in code; breaking them only gets your action blocked)
- Food orders: Cash on Delivery only. Before placing an order, send the owner the restaurant, items and total; the
  order button asks the owner to approve. Keep orders at or under ₹1,500 unless the owner says otherwise.
- Flights and hotels: search, compare and fill details, but never book or pay. Stop at the payment page and send the
  owner the summary (price, dates, times) and the link; they pay themselves.
- Never type card numbers, CVV, OTP or UPI PIN, never act on payment-gateway pages, never read credentials.
- Public URLs for things you build need the owner's approval; publish only the project folder.
- The owner's mood is private: use it to recommend things, never send it to any site or person.
- If the owner says STOP, everything is blocked until they say RESUME."""


def _audit(rec: dict) -> None:
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        with (HOME / "audit.jsonl").open("a") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}, ensure_ascii=False) + "\n")
    except OSError:
        pass  # an audit write failure must not stop the agent; the rails above still apply


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_system_prompt_section("rails.owner-rules", RULES + (UPI_RULES if upi_id() else ""), position="after_memory", max_chars=2000)
