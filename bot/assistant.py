"""One owner message in, one reply out. Code decides the steps; the model makes one call per step.

1. STOP / RESUME (code; while stopped nothing else runs).
2. A booking waiting on the owner: YES confirms, NO cancels, anything else is read as more details for it
   (one model call: task_update); a new request instead drops it.
3. Otherwise one model call with the owner's memory (rules, taste, mood, what they said before), answered as JSON
   forced to DECIDE (vLLM json_schema): reply, book (a form), places (going out) or read_swiggy_orders.
Questions and summaries for bookings are written by code (booking.py), never by the model. No tool calling and no
hand parsing of free text: those were the main sources of live bugs.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

import booking as B
import brain
import food
import login
import places

YES = re.compile(r"^\s*(yes|y|yep|yeah|haan|ha|ok(ay)?|go ahead|confirm(ed)?|do it|sure)\b[\s.!]*$", re.I)
NO = re.compile(r"^\s*(no|n|nope|cancel|stop it|don'?t|nahi|na)\b[\s.!]*$", re.I)
ASK = {"date": "Which date?", "from": "From where?", "to": "To where?", "passengers": "Who's travelling (names)?",
       "class": "Which class (SL, 3A, 2A, 1A)?", "max_price": "What's the most you want to pay?",
       "max_total": "What's your budget?", "max_price_per_night": "What's your budget per night?",
       "checkin": "Check-in date?", "checkout": "Check-out date?", "guests": "Who's staying (names)?",
       "city": "Which city?", "dish": "What would you like to eat?", "drop": "Where to?", "pickup": "Pick up from where?",
       "when": "When?", "arrive": "When do you arrive?", "leave": "When do you leave?"}

PERSONA = """You are the owner's personal assistant on WhatsApp/Telegram. Today is {today}.
Decide what to do with the owner's message and answer as JSON:
- action "book": any booking or order (flight, train, bus, cab, hotel, food delivery, trip). Set kind, and in
  details ONLY what the owner said in this message (dates and people exactly as said, e.g. "friday", "me and mom").
  Detail names per kind: {kinds}. A named dish ("want to eat chole bhature", "craving pizza", "order biryani")
  is an order: book, kind food, even without the word order.
- action "places": places to go out to eat or visit near them (cuisine if they said one).
- action "login": they ask to log in / sign in to a site (put the site's name in site).
- action "suggest_food": they ask WHAT to eat and name no dish ("what should I eat?", "any food ideas?").
- action "read_swiggy_orders": ONLY when they ask to read, check or refresh their Swiggy order history.
- action "reply": anything else; write the reply: brief and plain, like a person. You only get this one reply:
  never say "one moment" or "let me check"; answer now. Never invent facts about the owner. NEVER say you searched,
  found, booked, ordered or logged in to anything: only the other actions do that, a reply just talks.
{memory}"""
VALUE = {"anyOf": [{"type": "string", "maxLength": 120}, {"type": "number"},
                   {"type": "array", "items": {"type": "string", "maxLength": 60}, "maxItems": 8}]}
# only real detail names: with any name allowed the model invented fields until max_tokens (measured live)
DETAILS = {"type": "object", "properties": {k: VALUE for k in dict.fromkeys(x for t in B.TEMPLATES.values() for x in t)},
           "additionalProperties": False}
DECIDE = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["reply", "book", "places", "login", "suggest_food", "read_swiggy_orders"]},
    "kind": {"type": "string", "enum": [*B.TEMPLATES, "none"]},
    "details": DETAILS,
    "cuisine": {"type": "string"},
    "site": {"type": "string", "maxLength": 40},
    "reply": {"type": "string", "maxLength": 1200}}, "required": ["action", "kind", "details", "reply"]}
IDEAS = {"type": "object", "properties": {"ideas": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
    "type": "object", "properties": {"dish": {"type": "string", "maxLength": 50}, "why": {"type": "string", "maxLength": 90}},
    "required": ["dish", "why"]}}}, "required": ["ideas"]}
PICK = re.compile(r"^\s*#?([1-3])\b\s*(.*)$", re.I)
CHAT = """You are the owner's personal assistant on WhatsApp/Telegram. Today is {today}. Reply in plain text, briefly,
like a person. Never invent facts about the owner; never say you searched, found, booked or ordered anything. You
only get this one reply: never say "one moment", "let me check" or that you'll come back.
{memory}"""
UPDATE = {"type": "object", "properties": {"action": {"type": "string", "enum": ["update", "new"]},
                                           "details": DETAILS},
          "required": ["action", "details"]}
FRESH_H = 12  # order history read this recently is reused, not re-read from Swiggy
SET_PHONE = re.compile(r"^\s*(set\s+)?(my\s+)?log ?in\s+phone(\s+number)?\s*(is|to|:|=)?\s*(\+?[\d][\d\s-]{8,15}\d)\s*$", re.I)
SET_GOOGLE = re.compile(r"^\s*(use\s+)?google\s+(for\s+)?log ?ins?\s*(with|:|=)?\s*([\w.+-]+@[\w-]+\.[\w.]+)\s*$", re.I)
LOGIN_TO = re.compile(r"^\s*(log ?in|sign ?in)\s+(to|on|into)?\s*(%s)\b" % "|".join(login.SITES), re.I)
SET_UPI = re.compile(r"^\s*(set\s+)?(my\s+)?upi(\s+id)?\s*(is|to|:|=)?\s*([\w.\-]{2,256}@[a-z][a-z0-9]{1,63})\s*$", re.I)
SET_CAP = re.compile(r"^\s*(set\s+)?(my\s+)?order\s+cap\s*(is|to|:|=)?\s*₹?\s*([\d,]{2,7})\s*$", re.I)
CAP = 1500  # ₹ per order unless the owner sets another (owner, 2026-10-22: "you decide, I'll tell you if it changes")
TRACK_EVERY_S, TRACK_FOR_S = 120, 2 * 3600
OTP = re.compile(r"^\s*(\d[\s-]?){4,8}\s*$")
# a message that is clearly not an answer to the open form's questions (live: "i am just asking to sign in" was
# taken as flight details): the form is dropped and the message routed afresh
LEAVE_FORM = re.compile(r"\b(log ?in|sign ?in|cancel|never ?mind|forget it|something else|stop that|not that|"
                        r"i (was|am) just asking)\b", re.I)
NOT_PLACES = {*login.SITES, "login", "log in", "signin", "sign in", "account", "google", "otp"}
PLACE_SLOTS = {"from", "to", "city", "pickup", "drop", "area"}


def _post(url: str, body: dict, timeout: float = 5) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


DATE_WORDS = re.compile(r"\b(today|tonight|tomorrow|day after tomorrow|(this |next |coming )?(mon|tues|wednes|thurs|fri|"
                        r"satur|sun)day)\b", re.I)
MONTHS = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
HAS_DATE = re.compile(rf"\b\d{{1,2}}(st|nd|rd|th)?\s*({MONTHS})|\b({MONTHS})[a-z]*\s*\d{{1,2}}\b|\d{{1,2}}/\d{{1,2}}|"
                      r"\d{4}-\d{2}-\d{2}", re.I)


def ground(store: B.Store, kind: str, text: str, slots: dict) -> dict:
    """Keep only what the message supports. The model invents (a 'trip to Rishikesh' came back with dates, a
    budget and interests) and converts weekdays wrongly ('Friday' -> a Sunday), so, measured live 2026-10-22:
    - a value stays only if the message has evidence for it (its words or digits; for dates, a date expression);
    - one weekday/relative day in the message ('friday', 'tomorrow') is turned into the date by code;
    - travellers the message names ('mom', 'me', a saved name) become the passengers when the model gave none."""
    low, out = text.lower(), {}
    slots = {k: v for k, v in slots.items()
             if not (k in PLACE_SLOTS and str(v).strip().lower() in NOT_PLACES)}  # "from goibibo to login" (live)
    digits = re.sub(r"[,\s]", "", low)
    words = set(re.findall(r"[a-z]+", low))
    for k, v in slots.items():
        if k in B.DATES:
            if DATE_WORDS.search(low) or HAS_DATE.search(low):
                out[k] = v
        elif k in ("passengers", "guests"):
            out[k] = v
        elif isinstance(v, list):
            kept = [x for x in v if set(re.findall(r"[a-z]+", str(x).lower())) & words]
            if kept:
                out[k] = kept
        elif isinstance(v, (int, float)) or re.fullmatch(r"[\d,.\s₹rsk]+", str(v), re.I):
            n = B._money(v)
            if n is not None and (str(int(n)) in digits or f"{n / 1000:g}k" in digits):
                out[k] = v
        elif set(re.findall(r"[a-z]+", str(v).lower())) & words or str(v).lower() in low:
            out[k] = v
        elif re.search(r"\d", str(v)) and any(d in digits for d in re.findall(r"\d+", str(v))):
            out[k] = v
    days = [m[0] for m in DATE_WORDS.finditer(low)]
    date_slot = {"cab": "when", "hotel": "checkin", "trip": "arrive"}.get(kind, "date")
    pair = {"hotel": ("checkin", "checkout"), "trip": ("arrive", "leave")}.get(kind)
    if len(days) == 1 and not HAS_DATE.search(low) and date_slot in B.TEMPLATES.get(kind, {}) and kind != "cab":
        out[date_slot] = days[0]
    elif len(days) == 2 and pair and not HAS_DATE.search(low):  # "this Saturday to Monday", in the order said
        out[pair[0]], out[pair[1]] = days
    counted = any(re.fullmatch(r"\s*\d+\s*", str(out.get(s, ""))) for s in ("passengers", "guests"))
    if kind in B.TEMPLATES and (counted or not any(s in out for s in ("passengers", "guests"))):
        who = []
        if re.search(r"\b(me|myself|i)\b", low):
            who.append("me")
        for key, f in store.facts("traveller:").items():
            name, tag = str(f["value"].get("name", "")), key.split(":", 1)[1]
            if tag != "self" and (re.search(rf"\b{re.escape(tag)}\b", low) or (name and name.lower() in low)):
                who.append(name or tag)
        slot = "guests" if "guests" in B.TEMPLATES[kind] else "passengers"
        if who and slot in B.TEMPLATES[kind] and (len(who) > 1 or who != ["me"]):
            out[slot] = who
    return out


class Assistant:
    def __init__(self, home: Path, store: B.Store, memory_url: str = "http://127.0.0.1:8765", chat=brain.chat,
                 post=_post, nearby=places.nearby_food, swiggy=None, logins=None):
        self.home, self.store, self.mem, self.post, self.nearby = Path(home), store, memory_url, post, nearby
        self.model, self.local = chat, threading.local()
        self.chat = self._traced  # every model call goes through the trace
        self.swiggy = swiggy or food.Swiggy(chat=self._traced, captures=self.home / "captures")
        self.notify = lambda text: None
        self.login = logins or login.Login()
        self.orders = food.Orders(chat=self._traced)
        self.home.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.home / "bot.db"), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS log (ts REAL, channel TEXT, chat TEXT, role TEXT, text TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS pending (chat TEXT PRIMARY KEY, task INTEGER)")
        self.db.execute("CREATE TABLE IF NOT EXISTS traces (ts REAL, chat TEXT, text TEXT, reply TEXT, ms INTEGER, "
                        "steps TEXT)")
        self.lock = threading.Lock()

    # log -------------------------------------------------------------------------------------------------------
    def _log(self, channel, chat, role, text):
        with self.lock:
            self.db.execute("INSERT INTO log VALUES (?,?,?,?,?)", (time.time(), channel, chat, role, text))
            self.db.commit()

    def recent(self, chat: str, n: int = 6) -> list[dict]:
        rows = self.db.execute("SELECT role, text FROM log WHERE chat=? ORDER BY ts DESC LIMIT ?", (chat, n)).fetchall()
        return [{"role": "user" if r == "owner" else "assistant", "content": t} for r, t in reversed(rows)]

    def log_days(self, days: int) -> list[tuple]:
        """For weekly-reflect: (utc time, owner|bot, text) over the last `days` days, all chats."""
        rows = self.db.execute("SELECT ts, role, text FROM log WHERE ts > ? ORDER BY ts", (time.time() - days * 86400,))
        return [(time.strftime("%Y-%m-%dT%H:%M", time.gmtime(ts)), role, text) for ts, role, text in rows]

    def _pending(self, chat, task=None, clear=False):
        with self.lock:
            if clear:
                self.db.execute("DELETE FROM pending WHERE chat=?", (chat,))
            elif task is not None:
                self.db.execute("INSERT OR REPLACE INTO pending VALUES (?,?)", (chat, task))
            self.db.commit()
            r = self.db.execute("SELECT task FROM pending WHERE chat=?", (chat,)).fetchone()
        return r[0] if r else None

    # memory ----------------------------------------------------------------------------------------------------
    def _memory(self, text: str) -> tuple[str, dict]:
        try:
            r = self.post(self.mem + "/search", {"query": text, "budget": 500, "skip": ["Hermes", "bot"],
                                                  "skip_chats": ["cron_"], "scope": "hermes"})
        except Exception:
            return self._with_taste([]), {}  # memory down: answer without it
        cur, parts = r.get("current", {}), []
        if r.get("rules"):
            parts.append("Standing rules from the owner (always follow):\n" + "\n".join(f"- {x['text']}" for x in r["rules"]))
        if r.get("warnings"):  # the owner corrected these before: Hermes showed them in every reply, so does the bot
            parts.append("Mistakes the owner corrected before (don't repeat them):\n" + "\n".join(
                f"- {w['error'] or w['check']}" + (f" (corrected {w['seen']}x)" if w.get("seen", 1) > 1 else "")
                for w in r["warnings"]))
        if cur.get("profile"):
            parts.append("Owner's taste (private):\n" + "\n".join(f"- {p['text']}" for p in cur["profile"]))
        if cur.get("mood"):
            parts.append(f"Owner's mood: {cur['mood']['mood']} ({cur['mood']['hours_ago']} h ago)")
        if r.get("context"):
            parts.append("What the owner said before:\n" + r["context"])
        return self._with_taste(parts), cur

    def _with_taste(self, parts: list) -> str:
        t = B.taste(self.store)
        if t.get("orders"):
            parts.append(f"What the owner orders on Swiggy ({t['orders']} orders): dishes {t['top_dishes']}, restaurants "
                         f"{t['top_restaurants']}, usual spend ₹{t['typical_spend']}")
        return "\n\n".join(parts)

    def _remember(self, chat: str, text: str):
        def go():
            try:
                self.post(self.mem + "/add", {"text": text, "speaker": "owner", "chat": chat, "detect_rule": True}, 90)
            except Exception:
                pass  # memory is fail-open
        threading.Thread(target=go, daemon=True).start()

    # the turn --------------------------------------------------------------------------------------------------
    def _traced(self, messages, **kw):
        t = time.time()
        try:
            out = self.model(messages, **kw)
        except Exception as e:
            self._step("model_error", ms=int(1000 * (time.time() - t)), error=repr(e)[:500])
            raise
        schema = kw.get("schema")
        self._step("model", ms=int(1000 * (time.time() - t)), schema=(list(schema.get("properties", {})) if schema else None),
                   out=(out.get("content") or "")[:1500])
        return out

    def _step(self, kind: str, **data):
        steps = getattr(self.local, "steps", None)
        if steps is not None:
            steps.append({"step": kind, **data})

    def handle(self, msg: dict, notify=None) -> str:
        """notify(text): an interim message (e.g. "Looking on Swiggy...") while a slow step runs. Every turn leaves a
        trace (each model call: schema, time, output; errors) in bot.db: read it with deploy/bot/trace.sh."""
        self.notify = notify or (lambda text: None)
        chat, text, t = msg["chat"], msg["text"], time.time()
        self.local.steps = []
        secret = bool(self.login.waiting_for(chat) and OTP.match(text))  # a login OTP: used, never stored
        shown = "[login OTP]" if secret else text
        self.local.secret = secret
        self._log(msg["channel"], chat, "owner", shown)
        reply = None
        try:
            reply = self._turn(chat, text)
        except Exception as e:
            self._step("error", error=repr(e)[:1000])
            raise
        finally:
            with self.lock:
                self.db.execute("INSERT INTO traces VALUES (?,?,?,?,?,?)", (t, chat, shown, reply,
                                int(1000 * (time.time() - t)), json.dumps(self.local.steps, ensure_ascii=False)))
                self.db.commit()
        self._log(msg["channel"], chat, "bot", reply)
        return reply

    def _turn(self, chat: str, text: str) -> str:
        self.local.chat = chat
        halt = self.home / "HALT"
        if text.strip().upper().rstrip(".!") == "STOP":
            halt.write_text(time.ctime())
            return "Stopped. Nothing runs until you say RESUME."
        if text.strip().upper().rstrip(".!") == "RESUME":
            halt.unlink(missing_ok=True)
            return "Resumed."
        if halt.exists():
            return "I'm stopped. Say RESUME to continue."
        if getattr(self.local, "secret", False):
            return self._finish_login(chat, text)
        m = SET_PHONE.match(text)
        if m:
            digits = re.sub(r"\D", "", m[5])[-10:]
            self.store.set_fact("owner:phone", digits, "owner")
            return f"Saved: I'll use your number ending {digits[-4:]} for logins. You'll get the OTPs; send them here."
        m = SET_UPI.match(text)
        if m:
            self.store.set_fact("owner:upi", m[5].lower(), "owner")
            return (f"Saved your UPI ID {m[5].lower()}. When you say 'order' on an option, I'll check the total with you, "
                    "then send the payment request there for you to approve.")
        m = SET_CAP.match(text)
        if m:
            cap = int(m[4].replace(",", ""))
            self.store.set_fact("owner:cap", cap, "owner")
            return f"Order cap set to ₹{cap}: I won't place an order above that."
        m = SET_GOOGLE.match(text)
        if m:
            self.store.set_fact("owner:google", m[4].lower(), "owner")
            return (f"Saved: on sites with 'Continue with Google' I'll use {m[4].lower()}. Sign in to that Google account "
                    "once in the bot's Chrome (Hermes desktop) so I can pick it.")
        m = LOGIN_TO.match(text)
        if m:
            return self._need_login(chat, m[3].lower(), None)
        self._remember(chat, text)
        stage = self.store.fact(f"stage:{chat}")
        if stage and not stage["stale"]:
            r = self._stage(chat, text, stage["value"])
            if r is not None:
                return r
        tid = self._pending(chat)
        if tid:
            if YES.match(text):
                self._pending(chat, clear=True)
                r = B.confirm(self.store, tid)
                if "error" in r:
                    return "I can't confirm that yet: " + r["error"]
                if self.store.task(tid)["kind"] == "food":
                    return self._compare(chat, r["slots"], tid)
                return ("Confirmed ✅ " + self._summary_line(r["slots"], self.store.task(tid)["kind"])
                        + "\nBooking it on the site comes with a later update; for now this is saved.")
            if NO.match(text):
                self._pending(chat, clear=True)
                B.finish(self.store, tid, "cancelled")
                return "Cancelled."
            t = self.store.task(tid)
            if LEAVE_FORM.search(text):
                self._pending(chat, clear=True)
                B.finish(self.store, tid, "cancelled")
                tid = None
            d = {} if tid is None else brain.extract([{"role": "system", "content": f"The owner is answering questions for a {t['kind']} "
                                f"booking with these details so far: {json.dumps(t['slots'])}. Detail names: "
                                f"{', '.join(B.TEMPLATES[t['kind']])}. If the message adds or changes details, action "
                                "update with only those (dates and people as said); if it is a different request, "
                                "action new."}, {"role": "user", "content": text}], UPDATE, self.chat, max_tokens=400)
            if tid and d.get("action") == "update" and d.get("details"):
                slots = ground(self.store, t["kind"], text, d["details"])
                return self._booking_reply(chat, B.update(self.store, tid, slots))
            if tid:
                self._pending(chat, clear=True)
                B.finish(self.store, tid, "cancelled")
        memory, cur = self._memory(text)
        kinds = "; ".join(f"{k}: {', '.join(v)}" for k, v in B.TEMPLATES.items())
        system = PERSONA.format(today=time.strftime("%A %d %B %Y"), memory=memory, kinds=kinds)
        talk = [*self.recent(chat)[:-1], {"role": "user", "content": text}]
        d = brain.extract([{"role": "system", "content": system}, *talk], DECIDE, self.chat, max_tokens=450)
        plain = CHAT.format(today=time.strftime("%A %d %B %Y"), memory=memory)  # free text: never the JSON prompt
        action = d.get("action", "reply")
        if action == "book" and d.get("kind") in B.TEMPLATES:
            slots = ground(self.store, d["kind"], text, d.get("details") or {})
            return self._booking_reply(chat, B.start(self.store, d["kind"], slots, str(d.get("site") or "")))
        if action == "login":
            site = re.sub(r"[^a-z]", "", str(d.get("site") or "").lower())
            site = next((k for k in login.SITES if k in site or (site and site in k)), None) or next(
                (k for k in login.SITES if k in text.lower()), None)
            if not site:
                return f"I can log in to {', '.join(login.SITES)}. Which one?"
            return self._need_login(chat, site, None)
        if action == "suggest_food":
            return self._ideas(chat, plain, talk)
        if action == "read_swiggy_orders":
            last = self.store.fact("swiggy:orders_read")
            stale = (not B.taste(self.store).get("orders")) if not last else last["age_h"] >= FRESH_H
            if stale or re.search(r"\b(refresh|re-?read|again|update|latest)\b", text, re.I):
                return self._read_orders()
            # read recently: the model chose this on a suggestion question (seen live): answer from memory
            return self._say(plain + "\n\nAnswer the owner's message using what they order on Swiggy (above), their "
                             "taste and mood. Suggest concrete dishes, briefly.", talk)
        if action == "places":
            found = self.nearby(cur.get("location"), 3000, d.get("cuisine", ""), limit=8)
            if "error" in found:
                return "Share your location on WhatsApp (attach → Location) and I'll look around you."
            return self._say(plain + "\n\nPick the 3 best for the owner from ONLY these real places (name, km, one "
                             f"line why, maps link):\n{json.dumps(found['places'])}", talk)
        reply = str(d.get("reply") or "").strip()
        if reply and reply in self._said(chat):  # low temperature repeats itself ("same joke 3x")
            reply = self._say(plain + "\n\nYou already gave this exact answer; give a different one.", talk, 0.9) or reply
        return reply or "Sorry, I didn't get that. Could you say it another way?"

    def _say(self, system: str, talk: list, temperature: float = 0.2) -> str:
        """Free text the owner reads as is (nothing parses it)."""
        msg = self.chat([{"role": "system", "content": system}, *talk], temperature=temperature)
        return (msg.get("content") or "").strip()

    def _said(self, chat: str, n: int = 5) -> list[str]:
        return [t for (t,) in self.db.execute("SELECT text FROM log WHERE chat=? AND role='bot' ORDER BY ts DESC LIMIT ?",
                                              (chat, n))]

    # Swiggy ----------------------------------------------------------------------------------------------------
    # logins ----------------------------------------------------------------------------------------------------
    def _need_login(self, chat: str, site: str, resume: dict | None) -> str:
        """A site wants a login: Google if set up and offered, else phone + OTP; then carry on with `resume`."""
        email = (self.store.fact("owner:google") or {}).get("value")
        if email and login.SITES.get(site, {}).get("google"):
            self.notify(f"{site.title()} wants a login: trying Google ({email})…")
            if self.login.google(site, email):
                return f"Logged in to {site.title()} with Google ✅\n" + self._resume(chat, resume)
        phone = (self.store.fact("owner:phone") or {}).get("value")
        if not phone:
            return (f"{site.title()} needs a login. Send 'login phone <your number>' once and I'll handle logins "
                    "from then on: you'll only send me the OTP.")
        self.notify(f"{site.title()} wants a login: entering your number ending {phone[-4:]}…")
        r = self.login.start(chat, site, phone)
        if r == "logged_in":
            return self._resume(chat, resume) if resume else f"You're already logged in to {site.title()}."
        if r != "otp_sent":
            return r
        self.store.set_fact(f"resume:{chat}", resume, "bot", ttl_h=0.2)
        return f"{site.title()} sent an OTP to your number ending {phone[-4:]}. Send it here (just the digits)."

    def _finish_login(self, chat: str, text: str) -> str:
        site = self.login.waiting_for(chat)
        phone = (self.store.fact("owner:phone") or {}).get("value", "")
        try:
            ok = self.login.finish(chat, re.sub(r"\D", "", text), phone)
        except food.cdp.Blocked as e:
            return str(e)
        if not ok:
            return f"That didn't log me in to {site.title()} (wrong or expired OTP?). Say 'login to {site}' to try again."
        resume = self.store.fact(f"resume:{chat}")
        return f"Logged in to {site.title()} ✅\n" + self._resume(chat, resume["value"] if resume and not resume["stale"] else None)

    def _resume(self, chat: str, resume: dict | None) -> str:
        if not resume:
            return "What would you like me to do?"
        if resume.get("do") == "orders":
            return self._read_orders()
        if resume.get("do") == "compare":
            return self._compare(chat, resume["slots"], resume.get("task"))
        if resume.get("do") == "find":
            t = self.store.task(resume["task"])
            return self._compare(chat, t["slots"], t["id"])
        return "Done."

    def _read_orders(self) -> str:
        self.notify("Reading your Swiggy order history…")
        try:
            orders = self.swiggy.orders()
        except food.LoggedOut:
            return self._need_login(self.local.chat, "swiggy", {"do": "orders"})
        except (food.NotReady, food.cdp.Blocked) as e:
            return str(e)
        if not orders:
            return "I opened your Swiggy orders but couldn't read any orders from the page."
        r = B.save_orders(self.store, "swiggy", orders)
        self.store.set_fact("swiggy:orders_read", len(orders), "swiggy")
        t = r["order_history"]
        dishes = ", ".join(f"{d} ({n})" for d, n in t["top_dishes"][:4])
        places_ = ", ".join(f"{p} ({n})" for p, n in t["top_restaurants"][:3])
        return (f"Saved {r['saved']} new orders ({t['orders']} in all). You mostly order {dishes}; from {places_}; "
                f"usually about ₹{t['typical_spend']:g}.")

    # food v2: ideas -> compare across apps + reviews -> choose -> link / cart -----------------------------------
    def _ideas(self, chat: str, plain: str, talk: list) -> str:
        """3 dishes from the owner's order pattern, taste, mood and the time of day; they pick one to compare."""
        got = brain.extract([{"role": "system", "content": plain + "\n\nSuggest exactly 3 dishes the owner could eat now, "
                              "based on what they order (above), their taste, diet and mood, and the time of day. "
                              f"It is {time.strftime('%H:%M')}. One short reason each."}, *talk], IDEAS, self.chat,
                            max_tokens=300)
        ideas = [i for i in got.get("ideas", []) if i.get("dish")][:3]
        if not ideas:
            return "Tell me a dish or two you feel like and I'll compare them across the apps."
        self.store.set_fact(f"stage:{chat}", {"stage": "ideas", "ideas": ideas}, "bot", ttl_h=1)
        return ("\n".join(f"{n}. {i['dish']} — {i['why']}" for n, i in enumerate(ideas, 1))
                + "\nReply 1, 2 or 3 and I'll compare it across the food apps near you, with reviews.")

    def _stage(self, chat: str, text: str, st: dict) -> str | None:
        """The owner answering a list the bot sent (ideas or options) or an order to confirm; None = not an answer,
        route normally."""
        if st["stage"] == "confirm_order":
            self.store.set_fact(f"stage:{chat}", None, "bot", ttl_h=0)
            if YES.match(text):
                return self._place(chat, st)
            if NO.match(text):
                return "Okay, not ordering. It's still in your Swiggy cart."
            return None
        m = PICK.match(text)
        if not m:
            self.store.set_fact(f"stage:{chat}", None, "bot", ttl_h=0)
            return None
        n = int(m[1])
        if st["stage"] == "ideas" and n <= len(st["ideas"]):
            self.store.set_fact(f"stage:{chat}", None, "bot", ttl_h=0)
            return self._compare(chat, {"dish": st["ideas"][n - 1]["dish"]}, None)
        if st["stage"] == "options" and n <= len(st["options"]):
            return self._choose(chat, st["options"][n - 1], m[2], st.get("task"))
        return None

    def _compare(self, chat: str, slots: dict, tid: int | None) -> str:
        dish = slots["dish"][0] if isinstance(slots.get("dish"), list) else slots.get("dish", "")
        apps = {name: self.swiggy for name, p in food.PLATFORMS.items() if p["verified"] and name == "swiggy"}
        asked = next((n for n in food.PLATFORMS if tid and n in str(self.store.task(tid).get("site") or "")), None)
        note = ""
        if asked and asked not in apps:  # live: "on Zomato" was searched on Swiggy without a word
            note = (f"{asked.title()} isn't connected to me yet (it blocks the server or I haven't checked its pages), "
                    f"so here's {', '.join(a.title() for a in apps)}.\n")
        self.notify(f"Comparing {dish} on {', '.join(apps)}, with reviews…")
        said = f"{slots.get('diet', '')} {dish}".lower()
        veg = bool(re.search(r"\bveg(etarian)?\b", said)) and not re.search(r"non[- ]?veg", said)
        try:
            top, failed = food.compare(dish, apps, slots.get("max_total"), veg)
        except food.LoggedOut:
            return self._need_login(chat, "swiggy", {"do": "compare", "slots": slots, "task": tid})
        except (food.NotReady, food.cdp.Blocked) as e:
            return str(e)
        if not top:
            if tid:
                B.finish(self.store, tid, "failed", f"no {dish} found", "swiggy")
            budget = f" under ₹{slots['max_total']:g}" if slots.get("max_total") else ""
            return f"I couldn't find {dish}{budget} on {', '.join(apps)} right now. Want something else?"
        for o in top:  # reviews: a bonus, never a blocker
            o["google"] = self.reviews(o["restaurant"])
        self.store.set_fact(f"stage:{chat}", {"stage": "options", "options": top, "task": tid}, "bot", ttl_h=1)
        lines = []
        for n, o in enumerate(top, 1):
            g = o.get("google")
            rev = (f" · Google ★{g['rating']:g} ({g['reviews']:g}): +{', '.join(g['liked'][:2])}"
                   + (f"; −{g['disliked'][0]}" if g.get("disliked") else "")) if g else ""
            lines.append(f"{n}. {o['item']} — {o['restaurant']} — ₹{o['price']:g} on {o['platform'].title()}"
                         + (f" ★{o['rating']}" if o.get("rating") else "") + (f" ({o['eta']})" if o.get("eta") else "")
                         + rev)
        tail = f"\n(Couldn't read {', '.join(failed)} this time.)" if failed else ""
        return (note + "Best options:\n" + "\n".join(lines) + tail
                + "\nReply with a number plus 'order' (I order it, you approve the UPI request), 'cart' (I fill your "
                "cart) or 'link' (you order), e.g. '1 order'.")

    def _prepare_order(self, chat: str, offer: dict, tid) -> str:
        """Cart, then the checkout page read back (items, total, address) for the owner's YES: nothing is paid yet."""
        upi = (self.store.fact("owner:upi") or {}).get("value")
        if not upi:
            return "Send 'upi id you@bank' once, then reply the option again with 'order'. I'll send the payment there."
        added = self._add_to_cart(chat, tid, offer)
        if "To pay" not in added:
            return added
        self.notify("Reading the checkout page…")
        summary = self.orders.summary()
        if not summary:
            return added + "\nI couldn't read the checkout page clearly, so I won't order. You can pay in Swiggy."
        cap = (self.store.fact("owner:cap") or {}).get("value", CAP)
        if summary["total"] > cap:
            return (f"The total is ₹{summary['total']:g}, over your order cap of ₹{cap}. It's in your Swiggy cart; pay "
                    "there, or send 'order cap <amount>' to raise the cap.")
        self.store.set_fact(f"stage:{chat}", {"stage": "confirm_order", "summary": summary, "upi": upi, "task": tid},
                            "bot", ttl_h=0.25)
        return (f"Order {', '.join(summary['items']) or offer['item']} from {offer['restaurant']}, delivered to "
                f"{summary['address']}, total ₹{summary['total']:g}, paid by a UPI request to {upi}? (YES / no)")

    def _place(self, chat: str, st: dict) -> str:
        total, upi = st["summary"]["total"], st["upi"]
        self.notify(f"Placing it: approve the UPI request for ₹{total:g} on your phone ({upi}).")
        try:
            r = self.orders.pay(upi, total)
        except food.cdp.Blocked as e:
            return f"Stopped by the safety rules: {e}. Nothing was paid. It's still in your Swiggy cart."
        if r != "paid":
            why = "you didn't approve the request in time" if r == "timeout" else r.replace("failed: ", "")
            return f"Not ordered: {why}. It's still in your Swiggy cart."
        if st.get("task"):
            B.finish(self.store, st["task"], "done", f"ordered ₹{total:g} by UPI", "swiggy")
        send = self.notify
        threading.Thread(target=self._track, args=(send,), daemon=True).start()
        return f"Paid and ordered ✅ ₹{total:g}. I'll send you updates until it arrives."

    def _track(self, send, sleep=time.sleep) -> None:
        """Order updates until delivered: only when the status changes; stops after 3 failed reads in a row."""
        last, fails, waited = None, 0, 0
        while waited < TRACK_FOR_S:
            sleep(TRACK_EVERY_S)
            waited += TRACK_EVERY_S
            try:
                st = self.orders.status()
                fails = 0
            except Exception:
                fails += 1
                if fails >= 3:
                    send("I can't read the order status any more. Check Swiggy for updates.")
                    return
                continue
            now = st.get("status", "")
            if now and now != last:
                eta = f", about {st['eta_minutes']:g} min away" if isinstance(st.get("eta_minutes"), (int, float)) else ""
                send(f"🛵 {now}{eta}")
                last = now
            if st.get("delivered"):
                return

    def reviews(self, restaurant: str):
        return food.google_reviews(restaurant)

    def _choose(self, chat: str, offer: dict, how: str, tid: int | None) -> str:
        self.store.set_fact(f"stage:{chat}", None, "bot", ttl_h=0)
        if re.search(r"\b(order|for me|place)\b", how, re.I) and offer.get("platform") == "swiggy":
            return self._prepare_order(chat, offer, tid)
        if re.search(r"\bcart\b", how, re.I) and offer.get("platform") == "swiggy":
            return self._add_to_cart(chat, tid, offer)
        return f"Here's {offer['item']} from {offer['restaurant']} on {offer['platform'].title()}: {offer.get('link')}"

    def _add_to_cart(self, chat: str, tid, offer: dict) -> str:
        self.notify(f"Adding {offer['item']} from {offer['restaurant']} to your Swiggy cart…")
        self._pending(chat, clear=True)
        if tid is None:
            tid = B.start(self.store, "food", {"dish": offer["item"], "max_total": float(offer["price"]) * 2})["task_id"]
        try:
            r = self.swiggy.add(offer)
        except food.LoggedOut:
            return self._need_login(chat, "swiggy", {"do": "find", "task": tid})
        except (food.NotReady, food.cdp.Blocked) as e:
            B.finish(self.store, tid, "failed", str(e), "swiggy")
            return str(e)
        if not r["in_cart"]:
            B.finish(self.store, tid, "failed", "item not in cart after ADD", "swiggy")
            return "I clicked ADD but don't see it in your cart. Please check Swiggy."
        B.finish(self.store, tid, "done", f"cart {offer['restaurant']} {offer['item']} ₹{r['total']}", "swiggy")
        return (f"In your Swiggy cart: {offer['item']} from {offer['restaurant']}. To pay: ₹{r['total'] or '?'}.\n"
                "I've stopped before ordering: open Swiggy to place it (cash on delivery or UPI).")

    def _summary_line(self, slots: dict, kind: str) -> str:
        return f"{kind.title()}: " + ", ".join(f"{k.replace('_', ' ')} {' + '.join(map(str, v)) if isinstance(v, list) else v}"
                                               for k, v in slots.items())

    def _booking_reply(self, chat: str, v: dict) -> str:
        if "error" in v:
            return "Sorry: " + v["error"]
        self._pending(chat, v["task_id"])
        if v.get("problems"):
            asks = [ASK.get(s, f"What's the {s}?") for s in v["missing"]]
            for p in v["problems"]:
                slot = p.split(":")[0]
                asks.insert(0, ASK.get(slot, f"What's the {slot}?") if "who they are" in p or "not a" in p else p + ".")
            return " ".join(dict.fromkeys(asks))
        if v.get("missing"):
            return " ".join(ASK.get(s, f"What's the {s.replace('_', ' ')}?") for s in v["missing"])
        return v["summary"] + " (YES / no)"
